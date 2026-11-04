/*
 * krbridge.dll - Kingdom Rush Lua bridge
 * Injects a Lua payload into the running LÖVE/LuaJIT game by IAT hooking
 * lua51.dll functions exported used by the exe / love.dll.
 *
 * Build:  python -m ziglang cc -target x86_64-windows-gnu -shared -O2 -o krbridge.dll krbridge.c
 */
#include <windows.h>

typedef struct lua_State lua_State;

typedef int(__cdecl *fn_loadbuffer)(lua_State *, const char *, size_t, const char *);
typedef int(__cdecl *fn_pcall)(lua_State *, int, int, int);
typedef int(__cdecl *fn_gettop)(lua_State *);

static fn_loadbuffer real_loadbuffer = NULL;
static fn_pcall      real_pcall = NULL;
static fn_gettop     real_gettop = NULL;

static void *target_gettop = NULL;
static void *target_loadbuffer = NULL;
static void *target_pcall = NULL;

static char g_dir[MAX_PATH];
static char g_payload[MAX_PATH];
static char g_log[MAX_PATH];
static char g_status[MAX_PATH];

static volatile LONG g_done = 0;

static int __cdecl hook_gettop(lua_State *L);
static int __cdecl hook_pcall(lua_State *L, int nargs, int nresults, int errfunc);
static int __cdecl hook_loadbuffer(lua_State *L, const char *b, size_t s, const char *n);

/* ---------- tiny string helpers (no CRT formatting) ---------- */
static void scat(char *dst, const char *src)
{
    while (*dst) dst++;
    while (*src) *dst++ = *src++;
    *dst = 0;
}

static void logline(const char *msg)
{
    HANDLE h = CreateFileA(g_log, FILE_APPEND_DATA, FILE_SHARE_READ, NULL,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w = 0;
        SetFilePointer(h, 0, NULL, FILE_END);
        WriteFile(h, msg, (DWORD)lstrlenA(msg), &w, NULL);
        WriteFile(h, "\r\n", 2, &w, NULL);
        CloseHandle(h);
    }
}

static void attach_marker(const char *msg)
{
    HANDLE h = CreateFileA(g_status, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w = 0;
        WriteFile(h, msg, (DWORD)lstrlenA(msg), &w, NULL);
        CloseHandle(h);
    }
}

/* ---------- IAT patching ---------- */
static int is_readable(const void *p, size_t len)
{
    MEMORY_BASIC_INFORMATION mbi;
    if (!p) return 0;
    if (VirtualQuery(p, &mbi, sizeof(mbi)) != sizeof(mbi)) return 0;
    if (mbi.State != MEM_COMMIT) return 0;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    return 1;
}

static volatile LONG g_hits = 0;
static volatile LONG g_mods = 0;

static void patch_one_module(HMODULE mod, void *target, void *repl)
{
    BYTE *base = (BYTE *)mod;
    if (!is_readable(base, 0x40)) return;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    if (!is_readable(base + dos->e_lfanew, sizeof(IMAGE_NT_HEADERS))) return;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;
    InterlockedIncrement(&g_mods);

    IMAGE_DATA_DIRECTORY dd =
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dd.VirtualAddress || dd.Size < sizeof(IMAGE_IMPORT_DESCRIPTOR)) return;
    if (!is_readable(base + dd.VirtualAddress, sizeof(IMAGE_IMPORT_DESCRIPTOR))) return;

    IMAGE_IMPORT_DESCRIPTOR *imp = (IMAGE_IMPORT_DESCRIPTOR *)(base + dd.VirtualAddress);
    for (int guard = 0; imp->Name && guard < 512; imp++, guard++) {
        if (!is_readable(imp, sizeof(IMAGE_IMPORT_DESCRIPTOR))) return;
        if (!imp->FirstThunk) continue;
        IMAGE_THUNK_DATA *iat = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        if (!is_readable(iat, sizeof(IMAGE_THUNK_DATA))) continue;
        for (int k = 0; k < 8192 && iat[k].u1.Function; k++) {
            if ((void *)iat[k].u1.Function == target) {
                DWORD old = 0;
                if (VirtualProtect(&iat[k].u1.Function, sizeof(void *),
                                   PAGE_READWRITE, &old)) {
                    iat[k].u1.Function = (ULONG_PTR)repl;
                    VirtualProtect(&iat[k].u1.Function, sizeof(void *), old, &old);
                    InterlockedIncrement(&g_hits);
                }
            }
        }
    }
}

typedef BOOL(WINAPI *fn_enum_modules)(HANDLE, HMODULE *, DWORD, LPDWORD);

static void patch_all_modules(void *target, void *repl)
{
    static fn_enum_modules enum_mods = NULL;
    if (!enum_mods) {
        HMODULE k = GetModuleHandleA("kernel32.dll");
        enum_mods = (fn_enum_modules)GetProcAddress(k, "K32EnumProcessModules");
    }

    /* Every module that statically imports lua51.dll */
    patch_one_module(GetModuleHandleA(NULL), target, repl);
    HMODULE love = GetModuleHandleA("love.dll");
    if (love) patch_one_module(love, target, repl);

    if (enum_mods) {
        HMODULE mods[512];
        DWORD needed = 0;
        if (enum_mods(GetCurrentProcess(), mods, sizeof(mods), &needed)) {
            DWORD count = needed / sizeof(HMODULE);
            if (count > 512) count = 512;
            for (DWORD i = 0; i < count; i++) patch_one_module(mods[i], target, repl);
        }
    }
}

/* ---------- payload runner ---------- */
static void run_payload(lua_State *L)
{
    HANDLE h = CreateFileA(g_payload, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        logline("bridge: payload.lua missing");
        return;
    }
    DWORD sz = GetFileSize(h, NULL);
    if (sz == 0 || sz > (16u << 20)) { CloseHandle(h); return; }
    char *buf = (char *)HeapAlloc(GetProcessHeap(), 0, sz + 1);
    if (!buf) { CloseHandle(h); return; }
    DWORD rd = 0;
    ReadFile(h, buf, sz, &rd, NULL);
    CloseHandle(h);
    buf[rd] = 0;

    if (!real_loadbuffer || !real_pcall) { HeapFree(GetProcessHeap(), 0, buf); return; }

    int rc = real_loadbuffer(L, buf, rd, "=krt_payload");
    if (rc == 0) {
        rc = real_pcall(L, 0, 0, 0);
        if (rc != 0) {
            logline("bridge: payload runtime error (see in-game log)");
        }
    } else {
        logline("bridge: payload compile error");
    }
    HeapFree(GetProcessHeap(), 0, buf);
}

static int __cdecl hook_gettop(lua_State *L)
{
    if (InterlockedCompareExchange(&g_done, 1, 0) == 0) {
        patch_all_modules((void *)hook_gettop, (void *)real_gettop);
        if (target_loadbuffer) patch_all_modules((void *)hook_loadbuffer, (void *)real_loadbuffer);
        if (target_pcall) patch_all_modules((void *)hook_pcall, (void *)real_pcall);
        logline("bridge: hooked via gettop, running payload");
        run_payload(L);
    }
    return real_gettop(L);
}

static int __cdecl hook_pcall(lua_State *L, int nargs, int nresults, int errfunc)
{
    if (InterlockedCompareExchange(&g_done, 1, 0) == 0) {
        patch_all_modules((void *)hook_gettop, (void *)real_gettop);
        if (target_loadbuffer) patch_all_modules((void *)hook_loadbuffer, (void *)real_loadbuffer);
        patch_all_modules((void *)hook_pcall, (void *)real_pcall);
        logline("bridge: hooked via pcall, running payload");
        run_payload(L);
    }
    return real_pcall(L, nargs, nresults, errfunc);
}

static int __cdecl hook_loadbuffer(lua_State *L, const char *b, size_t s, const char *n)
{
    if (InterlockedCompareExchange(&g_done, 1, 0) == 0) {
        patch_all_modules((void *)hook_gettop, (void *)real_gettop);
        patch_all_modules((void *)hook_loadbuffer, (void *)real_loadbuffer);
        if (target_pcall) patch_all_modules((void *)hook_pcall, (void *)real_pcall);
        logline("bridge: hooked via loadbuffer, running payload");
        run_payload(L);
    }
    return real_loadbuffer(L, b, s, n);
}

/* ---------- init ---------- */
static void init_paths(void)
{
    char tmp[MAX_PATH];
    DWORD n = GetEnvironmentVariableA("LOCALAPPDATA", tmp, MAX_PATH);
    if (!n || n >= MAX_PATH) lstrcpyA(tmp, "C:\\Windows\\Temp");
    lstrcpyA(g_dir, tmp);
    scat(g_dir, "\\KR_Trainer");

    CreateDirectoryA(g_dir, NULL);

    lstrcpyA(g_payload, g_dir); scat(g_payload, "\\payload.lua");
    lstrcpyA(g_log, g_dir);     scat(g_log, "\\bridge.log");
    lstrcpyA(g_status, g_dir);  scat(g_status, "\\bridge.status");
}

static int do_patch(void)
{
    HMODULE lua = GetModuleHandleA("lua51.dll");
    if (!lua) { logline("bridge: lua51.dll not loaded"); return 0; }

    real_loadbuffer = (fn_loadbuffer)GetProcAddress(lua, "luaL_loadbuffer");
    real_pcall      = (fn_pcall)GetProcAddress(lua, "lua_pcall");
    real_gettop     = (fn_gettop)GetProcAddress(lua, "lua_gettop");
    if (!real_loadbuffer || !real_pcall || !real_gettop) {
        logline("bridge: exports missing");
        return 0;
    }
    target_loadbuffer = (void *)real_loadbuffer;
    target_gettop = (void *)real_gettop;
    target_pcall = (void *)real_pcall;

    patch_all_modules(target_gettop, (void *)hook_gettop);
    patch_all_modules(target_loadbuffer, (void *)hook_loadbuffer);
    patch_all_modules(target_pcall, (void *)hook_pcall);

    {
        char msg[128];
        char num[24];
        int v = g_hits, m = g_mods, i = 0;
        if (v == 0) { num[i++] = '0'; }
        while (v > 0 && i < 20) { num[i++] = (char)('0' + v % 10); v /= 10; }
        num[i] = 0;
        for (int a = 0, b = i - 1; a < b; a++, b--) { char t = num[a]; num[a] = num[b]; num[b] = t; }
        lstrcpyA(msg, "bridge: iat hits=");
        scat(msg, num);
        i = 0; v = m;
        if (v == 0) { num[i++] = '0'; }
        while (v > 0 && i < 20) { num[i++] = (char)('0' + v % 10); v /= 10; }
        num[i] = 0;
        for (int a = 0, b = i - 1; a < b; a++, b--) { char t = num[a]; num[a] = num[b]; num[b] = t; }
        scat(msg, " modules=");
        scat(msg, num);
        logline(msg);
    }
    logline("bridge: IAT patched, waiting for lua call");
    attach_marker("patched");
    return 1;
}

static DWORD WINAPI bridge_thread(LPVOID param)
{
    (void)param;
    do_patch();
    attach_marker("patched");
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    (void)inst; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        init_paths();
        logline("bridge: attached");
        HANDLE th = CreateThread(NULL, 0, bridge_thread, NULL, 0, NULL);
        if (th) CloseHandle(th);
    }
    return TRUE;
}
