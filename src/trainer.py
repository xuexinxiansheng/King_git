# -*- coding: utf-8 -*-
"""
Kingdom Rush 修改器 — 新粗野主义界面（V4，四大功能模块）

米白底 + 近黑文字 + 黑色粗边框 + 硬阴影，黄/粉/绿/蓝四色点缀。
硬阴影通过自绘偏移实心矩形实现（Qt QSS 不支持 box-shadow）。

四个模块（01 金币 / 02 防御塔 / 03 英雄与友军 / 04 全局设置）
通过 state.txt 下发开关，游戏内 payload.lua 生效；
防御塔清单优先读 payload 写出的 towers.txt，读不到则用内置数据。
"""

import ctypes
import ctypes.wintypes as w
import os
import re
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import (Qt, QTimer, QThread, Signal, QPointF, QRectF, QSize)
from PySide6.QtGui import (QColor, QFont, QIcon, QIntValidator, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap,
                           QRadialGradient, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QTextEdit, QFileDialog,
                               QSizeGrip, QDialog,
                               QStackedWidget, QSizePolicy)

APP_NAME = "Kingdom Rush 修改器"
APPID = "246420"
GAME_EXE = "Kingdom Rush.exe"
GAME_DIR_NAME = "Kingdom Rush"
BRIDGE_DIR = os.path.join(os.environ["LOCALAPPDATA"], "KR_Trainer")

INK = "#111111"          # 主文字
INK_DIM = "#555555"      # 次要文字
INK_FAINT = "#8a8a8a"    # 弱化文字
GOLD = "#ffe500"         # 黄色强调
PINK = "#ff6bcb"         # 粉色
BLUE = "#4dabf7"         # 蓝色
LIME = "#b2f252"         # 绿色
BG = "#f5f0e8"           # 米白背景

FONT_FAMILIES = ["Bahnschrift", "Microsoft YaHei UI", "Microsoft YaHei"]

# 防御塔四大系（4 级进阶与分支并入本系，界面只显示 4 行）
# 兵营系放最后：它不自己攻击，只有「范围」可调
FAMILIES = (
    ("archer", "弓兵系", GOLD),
    ("mage", "法师系", PINK),
    ("engineer", "火炮系", BLUE),
    ("barrack", "兵营系", LIME),
)
FAMILY_INFO = {key: (name, color) for key, name, color in FAMILIES}
# 塔型 → 系（兼容没有 family 列的旧 towers.txt：4 级进阶与分支并入原系）
TYPE_FAMILY = {
    "archer": "archer", "ranger": "archer", "musketeer": "archer",
    "mage": "mage", "sorcerer": "mage", "arcane_wizard": "mage",
    "barrack": "barrack", "paladin": "barrack", "barbarian": "barrack", "elf": "barrack",
    "engineer": "engineer", "tesla": "engineer", "bfg": "engineer",
}
# 01 修改设置 内的三个分页（金币已并入全局设置）
TABS = ("全局设置", "防御塔", "英雄与友军")

# 内置塔数据（与 towers.txt 同格式）：无游戏/未扫描时界面也能直接建表，
# 只是显示用的基准值；实际改数值由 payload 读游戏内模板，不依赖本表。
TOWERS_BUILTIN = """# tpl|zh|type|level|price|cd|dmg_min|dmg_max|range|bullet|family
tower_archer_1|弓兵塔|archer|1|70|0.800|4.00|6.00|140.00|arrow_1|archer|0
tower_archer_2|射手塔|archer|2|110|0.600|7.00|11.00|160.00|arrow_2|archer|0
tower_archer_3|神射手|archer|3|160|0.500|10.00|16.00|180.00|arrow_3|archer|0
tower_mage_1|法师塔|mage|1|100|1.500|9.00|17.00|140.00|bolt_1|mage|0
tower_mage_2|术士塔|mage|2|160|1.500|23.00|43.00|160.00|bolt_2|mage|0
tower_mage_3|巫师塔|mage|3|240|1.500|40.00|74.00|180.00|bolt_3|mage|0
tower_barrack_1|民兵兵营|barrack|1|70|0.000|0.00|0.00|145.00||barrack|3
tower_barrack_2|步兵兵营|barrack|2|110|0.000|0.00|0.00|145.00||barrack|3
tower_barrack_3|骑士兵营|barrack|3|160|0.000|0.00|0.00|145.00||barrack|3
tower_engineer_1|矮人炮塔|engineer|1|125|3.000|8.00|15.00|160.00|bomb|engineer|0
tower_engineer_2|进阶火炮|engineer|2|220|3.000|20.00|40.00|160.00|bomb_dynamite|engineer|0
tower_engineer_3|矮人榴弹炮|engineer|3|320|3.000|30.00|60.00|179.20|bomb_black|engineer|0
tower_arcane_wizard|奥术防御塔|arcane_wizard|1|300|2.000|76.00|140.00|200.00|ray_arcane|mage|0
tower_barbarian|蛮族大殿|barbarian|1|230|0.000|0.00|0.00|145.00||barrack|3
tower_bfg|500mm大贝莎|bfg|1|400|3.650|50.00|100.00|180.00|bomb_bfg|engineer|0
tower_elf|精灵塔|elf|1|100|0.000|0.00|0.00|145.00||barrack|4
tower_musketeer|火枪手要塞|musketeer|1|230|1.500|35.00|65.00|235.00|shotgun_musketeer|archer|0
tower_paladin|神圣秩序|paladin|1|230|0.000|0.00|0.00|145.00||barrack|3
tower_ranger|游侠匿所|ranger|1|230|0.400|13.00|19.00|200.00|arrow_ranger|archer|0
tower_sasquash|Sasquash|sasquash|1|0|0.000|0.00|0.00|288.00|||0
tower_sorcerer|魔法师|sorcerer|1|300|1.500|42.00|78.00|200.00|bolt_sorcerer|mage|0
tower_tesla|特拉斯x104|tesla|1|375|2.200|60.00|110.00|165.00|ray_tesla|engineer|0
"""
TW_COL_W = 100      # 攻速/伤害/范围 列宽，表头与数据行共用，保证对齐

# 游戏内脚本版本落后时，通过 cmd.txt 让它在进程里重载新载荷。
# 已注入的进程不会因为再注入一次 DLL 而重新执行 DllMain，只能这样就地换。
PAYLOAD_SYNC = r'''local p = (os.getenv("LOCALAPPDATA") or ".") .. "\\KR_Trainer\\payload.lua"
local h = io.open(p, "rb")
if h then
  local src = h:read("*a")
  h:close()
  local f = loadstring(src, "=payload")
  if f then pcall(f) end
end
return "krt-sync"'''


def asset(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name).replace("\\", "/")


# ------------------------------------------------------------------ win32
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateToolhelp32Snapshot.restype = w.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
k32.OpenProcess.restype = w.HANDLE
k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k32.CloseHandle.argtypes = [w.HANDLE]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, w.DWORD]
k32.WriteProcessMemory.restype = w.BOOL
k32.WriteProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
k32.GetModuleHandleW.restype = w.HMODULE
k32.GetModuleHandleW.argtypes = [w.LPCWSTR]
k32.GetProcAddress.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [w.HMODULE, ctypes.c_char_p]
k32.CreateRemoteThread.restype = w.HANDLE
k32.CreateRemoteThread.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                   ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
k32.WaitForSingleObject.restype = w.DWORD
k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k32.GetExitCodeThread.restype = w.BOOL
k32.GetExitCodeThread.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
k32.Process32First.argtypes = [w.HANDLE, ctypes.c_void_p]
k32.Process32Next.argtypes = [w.HANDLE, ctypes.c_void_p]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD),
        ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def list_pids(name=GAME_EXE):
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    e = PROCESSENTRY32()
    e.dwSize = ctypes.sizeof(e)
    out = []
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.decode("latin1").lower() == name.lower():
            out.append(e.th32ProcessID)
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def inject_dll(pid, dll_path):
    h = k32.OpenProcess(0x1F0FFF, False, pid)
    if not h:
        return False, "OpenProcess 失败 (错误码 %d)" % ctypes.get_last_error()
    try:
        buf = str(dll_path).encode("utf-16-le") + b"\x00\x00"
        addr = k32.VirtualAllocEx(h, None, len(buf), 0x3000, 0x04)
        if not addr:
            return False, "VirtualAllocEx 失败"
        written = ctypes.c_size_t(0)
        if not k32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, len(buf), ctypes.byref(written)):
            return False, "WriteProcessMemory 失败"
        loadlib = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        tid = w.DWORD(0)
        th = k32.CreateRemoteThread(h, None, 0, loadlib, ctypes.c_void_p(addr), 0, ctypes.byref(tid))
        if not th:
            return False, "CreateRemoteThread 失败 (错误码 %d)" % ctypes.get_last_error()
        k32.WaitForSingleObject(th, 10000)
        ec = w.DWORD(0)
        k32.GetExitCodeThread(th, ctypes.byref(ec))
        k32.CloseHandle(th)
        if ec.value == 0:
            return False, "LoadLibrary 返回失败"
        return True, "ok"
    finally:
        k32.CloseHandle(h)


def steam_libraries():
    roots = []
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        p, _ = winreg.QueryValueEx(key, "SteamPath")
        roots.append(p.replace("/", "\\"))
    except Exception:
        pass
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
                  r"D:\Steam", r"D:\steam", r"E:\Steam"):
        if os.path.isdir(guess):
            roots.append(guess)
    libs = []
    for root in roots:
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                txt = open(vdf, "r", encoding="utf-8", errors="replace").read()
                for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                    libs.append(m.group(1).replace("\\\\", "\\"))
            except Exception:
                pass
        libs.append(root)
    seen, out = set(), []
    for p in libs:
        q = os.path.normpath(p)
        if q.lower() not in seen and os.path.isdir(q):
            seen.add(q.lower())
            out.append(q)
    return out


def detect_game_dir():
    for lib in steam_libraries():
        cand = os.path.join(lib, "steamapps", "common", GAME_DIR_NAME)
        if os.path.isfile(os.path.join(cand, GAME_EXE)):
            return cand
    for drive in "CDEFG":
        cand = os.path.join(drive + ":\\", "steam", "steamapps", "common", GAME_DIR_NAME)
        if os.path.isfile(os.path.join(cand, GAME_EXE)):
            return cand
    return ""


def read_version(game_dir):
    slot = os.path.join(os.environ["APPDATA"], "kingdom_rush", "slot_1.lua")
    try:
        txt = open(slot, "r", encoding="utf-8", errors="replace").read()
        m = re.search(r'version_string"\]\s*=\s*"([^"]+)"', txt)
        if m:
            return m.group(1).replace("kr1-desktop-", "")
    except Exception:
        pass
    return "6.4.46"


def font(size, weight=QFont.Normal, spacing=0.0):
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(int(round(size)))
    f.setWeight(weight)
    f.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text))


# ------------------------------------------------------------------ widgets
class Dot(QWidget):
    def __init__(self, color=GOLD, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(13, 13)

    def setColor(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.color
        glow = QRadialGradient(QRectF(self.rect()).center(), 6.5)
        glow.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 90))
        glow.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        p.setBrush(c)
        p.drawEllipse(QRectF(4, 4, 5, 5))


class Mark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, 19, 19)
        p.setBrush(QColor(PINK))
        p.setPen(QPen(QColor("#111111"), 2))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#111111"))
        p.drawRoundedRect(QRectF(7.5, 7.5, 5, 5), 1.5, 1.5)


class NeoCard(QWidget):
    """新粗野主义卡片：白底 + 黑粗边 + 右下硬阴影。

    描边在路径上居中绘制，所以路径必须内缩半个描边宽，否则一半描边会被
    控件边界裁掉（表现为边缘只有 1px、且锯齿明显）。
    """

    BORDER = 3
    OFFSET = 6                      # 阴影偏移：右下比左上多出约 4.5px，才看得出是投影

    def __init__(self, radius=12, shadow="#111111", parent=None):
        super().__init__(parent)
        self.radius = radius
        self.shadow = QColor(shadow)

    def _path(self, rect):
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        return path

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        half = self.BORDER / 2.0
        inset = half + 0.5
        body = QRectF(inset, inset,
                      self.width() - 2 * inset - self.OFFSET,
                      self.height() - 2 * inset - self.OFFSET)
        # 阴影：与主体同形，整体右下平移
        p.fillPath(self._path(body.translated(self.OFFSET, self.OFFSET)), self.shadow)
        p.fillPath(self._path(body), QColor("#ffffff"))
        p.setPen(QPen(self.shadow, self.BORDER))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._path(body))


class ShadowWrap(QWidget):
    """给按钮套一层与卡片同款的右下硬阴影。

    阴影由父控件（本类）自绘一个偏移的圆角实心矩形，子控件保持**均匀**边框；
    不能用「右下边框加粗」冒充阴影——那样圆角处两条边的宽度不一致，会错位。
    """

    def __init__(self, child, dx=3, dy=3, radius=8, color=INK, parent=None):
        super().__init__(parent)
        self.child = child
        self.dx, self.dy = dx, dy
        self.radius = radius
        self.color = QColor(color)
        child.setParent(self)
        child.move(0, 0)
        child.show()                 # 子控件不在布局里，必须显式显示，否则只剩阴影
        fixed = child.minimumWidth() == child.maximumWidth() and child.width() > 0
        if fixed:
            self.setFixedSize(child.width() + dx, child.height() + dy)
        else:
            self.setSizePolicy(child.sizePolicy().horizontalPolicy(),
                               child.sizePolicy().verticalPolicy())

    def _need(self):
        """子控件实际需要的尺寸：QSS 下 sizeHint 可能小于 setFixedHeight 后的高度。"""
        c = self.child
        w = max(c.minimumWidth(), c.sizeHint().width())
        h = max(c.minimumHeight(), c.sizeHint().height())
        return max(w, 1), max(h, 1)

    def sizeHint(self):
        w, h = self._need()
        return QSize(w + self.dx, h + self.dy)

    def minimumSizeHint(self):
        return self.sizeHint()

    def resizeEvent(self, e):
        self.child.setGeometry(0, 0, max(0, self.width() - self.dx),
                               max(0, self.height() - self.dy))
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(self.dx, self.dy,
                                 self.width() - self.dx, self.height() - self.dy),
                          self.radius, self.radius)


class WinButton(QWidget):
    """窗口控制按钮：最小化 / 最大化 / 关闭。矢量图标，悬停反馈与 Windows 一致。"""
    clicked = Signal()

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(32, 32)   # 30 主体 + 2 阴影（补偿 2px 描边外扩后可见约 1px）
        self.setCursor(Qt.ArrowCursor)
        self._hover = False
        self._press = False

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._press = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = True
            self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        was = self._press
        self._press = False
        self.update()
        if was and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # 主体内缩半个描边，避免 2px 描边被控件边界裁掉（会显得边缘发虚、有锯齿）
        body_rect = QRectF(1.5, 1.5, 27, 27)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#111111"))
        p.drawRoundedRect(body_rect.translated(2, 2), 6, 6)      # 右下硬阴影
        body = QColor("#e2382c") if self.kind == "close" else QColor("#ffffff")
        if self._press:
            body = body.darker(108)
        elif self._hover:
            body = QColor("#ffe500") if self.kind != "close" else QColor("#ff4d3a")
        p.setBrush(body)
        p.setPen(QPen(QColor("#111111"), 2))
        p.drawRoundedRect(body_rect, 6, 6)
        ink = QColor("#ffffff") if self.kind == "close" else QColor("#111111")
        p.setPen(QPen(ink, 3, Qt.SolidLine, Qt.RoundCap))
        cx, cy = self.width() / 2.0, self.height() / 2.0
        cx -= 1                                  # 主体偏左上，图标跟着偏一点
        cy -= 1
        if self.kind == "min":
            p.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
        elif self.kind == "max":
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 1, 1)
        else:
            p.drawLine(QPointF(cx - 4.5, cy - 4.5), QPointF(cx + 4.5, cy + 4.5))
            p.drawLine(QPointF(cx + 4.5, cy - 4.5), QPointF(cx - 4.5, cy + 4.5))


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self._drag_ofs = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(GOLD))
        p.setPen(QPen(QColor("#111111"), 3))
        p.drawLine(0, self.height() - 2, self.width(), self.height() - 2)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_ofs = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_ofs is not None and (e.buttons() & Qt.LeftButton):
            self.window().move(e.globalPosition().toPoint() - self._drag_ofs)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_ofs = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        win = self.window()
        win.showNormal() if win.isMaximized() else win.showMaximized()


class CheckBox(QWidget):
    """方形勾选框：白底黑边，选中粉色 + 黑勾。"""
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._on = bool(checked)
        self.setFixedSize(20, 20)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self):
        return self._on

    def setChecked(self, on):
        on = bool(on)
        if on != self._on:
            self._on = on
            self.update()
            self.toggled.emit(on)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setChecked(not self._on)
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, 19, 19)
        if not self.isEnabled():                 # 不可用：整体置灰
            p.setBrush(QColor("#f2f2f2"))
            p.setPen(QPen(QColor("#cccccc"), 2))
            p.drawRoundedRect(r, 6, 6)
            return
        p.setBrush(QColor(PINK) if self._on else QColor("#ffffff"))
        p.setPen(QPen(QColor("#111111"), 3))
        p.drawRoundedRect(r, 6, 6)
        if self._on:
            p.setPen(QPen(QColor("#111111"), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(6, 10), QPointF(9, 13))
            p.drawLine(QPointF(9, 13), QPointF(14, 6))


class FuncRow(QWidget):
    """功能行：标题 + 说明 + 数值框 + 勾选框；开启时整行高亮并显示绿边。"""
    changed = Signal()

    def __init__(self, title, hint, has_value=False, value="", checked=False,
                 lock_edit=True, parent=None):
        super().__init__(parent)
        self._has_value = has_value
        self._lock_edit = lock_edit
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 12, 0)
        h.setSpacing(10)

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(font(12.5, QFont.DemiBold))
        self.hint_lbl = QLabel(hint)
        self.hint_lbl.setFont(font(10.5))
        box.addWidget(self.title_lbl)
        box.addWidget(self.hint_lbl)
        text = QWidget()
        text.setLayout(box)
        h.addWidget(text, 0, Qt.AlignVCenter)   # 行变高时文字块垂直居中
        h.addStretch(1)

        self.edit = None
        if has_value:
            self.edit = QLineEdit(value)
            self.edit.setObjectName("val")
            self.edit.setFixedSize(86, 30)
            self.edit.setFont(font(13, QFont.DemiBold))
            self.edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.edit.textChanged.connect(self.changed)
            h.addWidget(self.edit)

        self.switch = CheckBox(checked)
        self.switch.toggled.connect(self._on_switch)
        h.addWidget(self.switch)

        self._sync_look()

    def _on_switch(self, on):
        self._sync_look()
        self.changed.emit()

    def _sync_look(self):
        on = self.switch.isChecked()
        if on:
            self.title_lbl.setStyleSheet("background:#b2f252;color:#111111;padding:2px 8px;")
        else:
            self.title_lbl.setStyleSheet("color:#555555;background:transparent;")
        self.hint_lbl.setStyleSheet("color:%s;background:transparent;" % ("#555555" if on else "#8a8a8a"))
        if self.edit is not None:
            self.edit.setProperty("on", on)
            self.edit.style().unpolish(self.edit)
            self.edit.style().polish(self.edit)
            self.edit.setEnabled(on or not self._lock_edit)
        self.update()

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, on):
        self.switch.setChecked(on)

    def value(self):
        return self.edit.text() if self.edit is not None else "0"

    def setValue(self, v):
        if self.edit is not None:
            self.edit.setText(str(v))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.switch.isChecked()
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if on:
            g = QLinearGradient(0, 0, 0, self.height())
            g.setColorAt(0, QColor(178, 242, 82, 90))
            g.setColorAt(1, QColor(178, 242, 82, 26))
            p.setBrush(g)
            p.setPen(QPen(QColor("#111111"), 1))
        else:
            p.setBrush(QColor(0, 0, 0, 5))
            p.setPen(QPen(QColor(0, 0, 0, 14), 1))
        p.drawRoundedRect(r, 10, 10)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(LIME))
            p.drawRoundedRect(QRectF(0.5, 10, 2.5, self.height() - 20), 1.25, 1.25)


class TowerCell(QWidget):
    """塔参数单元格：勾选框 + 数值输入框（倍数，或士兵数量这类定量值）。"""
    changed = Signal()

    def __init__(self, value="2", enabled=True, parent=None, label="", minimum=None, maximum=None,
                 tip=""):
        super().__init__(parent)
        self.setFixedHeight(28)
        if tip:
            self.setToolTip(tip)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.switch = CheckBox(False)
        self.switch.toggled.connect(self._sync)
        if not enabled:
            self.switch.setEnabled(False)
            self.switch.setCursor(Qt.ForbiddenCursor)
        h.addWidget(self.switch)

        if label:
            cap = QLabel(label)
            cap.setFont(font(10.5, QFont.DemiBold))
            cap.setStyleSheet("color:%s;background:transparent;" % INK_DIM)
            h.addWidget(cap)

        self._fallback = str(value)
        self.edit = QLineEdit(value)
        self.edit.setObjectName("tw")
        self.edit.setFixedSize(48, 26)
        self.edit.setFont(font(11.5, QFont.DemiBold))
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.textChanged.connect(self.changed)
        self.edit.setEnabled(enabled)      # 属性适用就能直接填倍数，勾选只决定是否生效
        if not enabled:
            self.edit.setEnabled(False)
            self.edit.clear()
            self.edit.setPlaceholderText("—")
        h.addWidget(self.edit)
        if minimum is not None or maximum is not None:
            self.edit.setValidator(QIntValidator(minimum if minimum is not None else 0,
                                                maximum if maximum is not None else 99, self))
        self.enabled = enabled

    def _sync(self, on):
        self.changed.emit()

    def isChecked(self):
        return self.switch.isChecked() and self.enabled

    def setChecked(self, on):
        self.switch.setChecked(on)

    def value(self):
        return (self.edit.text() or self._fallback).strip()

    def setValue(self, v):
        self.edit.setText(str(v))


class TowerRow(QWidget):
    """一行防御塔系：色条 + 系名 + 成员/原始参数 + 数值格。

    普通系是「攻速 / 伤害 / 范围」三格；兵营系不自己攻击，改成「士兵数量 + 范围」：
    士兵数量占前两列宽度，和表头列宽对齐。
    """
    changed = Signal()

    def __init__(self, key, title, info, has_attack, tip="", color=INK_FAINT,
                 barrack=False, soldiers=0, parent=None):
        super().__init__(parent)
        self.tpl = key
        self.barrack = barrack
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        if tip:
            self.setToolTip(tip)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 8, 0)
        h.setSpacing(8)

        bar = QWidget()
        bar.setFixedSize(4, 18)
        bar.setStyleSheet("background:%s;border-radius:2px;" % color)
        h.addWidget(bar)

        name = QLabel(title)
        name.setFont(font(12.5, QFont.DemiBold))
        name.setStyleSheet("color:%s;background:transparent;" % INK)
        name.setFixedWidth(70)
        h.addWidget(name)

        sub = QLabel(info)
        sub.setFont(font(10.5))
        sub.setStyleSheet("color:%s;background:transparent;" % INK_FAINT)
        h.addWidget(sub)
        h.addStretch(1)
        self.cell_spd = None
        self.cell_dmg = None
        self.cell_sold = None
        self.cell_rng = TowerCell("2", True)
        cols = []
        if barrack:
            self.cell_sold = TowerCell(
                str(soldiers or 3), True, label="士兵数量", minimum=1, maximum=20,
                tip="每个兵营的士兵数（1–20）。兵营按这个值自动补人，升级时全部士兵一起升级；\n"
                    "从 6 改到 8 后，多出的 2 人由兵营从门里走出（新人只能这么出场）。\n"
                    "调小后多出来的士兵会保留到自然死亡，不会立刻消失。")
            cols.append((self.cell_sold, TW_COL_W * 2))
        else:
            self.cell_spd = TowerCell("2", has_attack)
            self.cell_dmg = TowerCell("2", has_attack)
            cols.append((self.cell_spd, TW_COL_W))
            cols.append((self.cell_dmg, TW_COL_W))
        cols.append((self.cell_rng, TW_COL_W))
        for cell, width in cols:
            col = QWidget()
            col.setFixedWidth(width)
            ch = QHBoxLayout(col)
            ch.setContentsMargins(0, 0, 0, 0)
            ch.setSpacing(0)
            ch.addStretch(1)
            ch.addWidget(cell)
            ch.addStretch(1)
            h.addWidget(col)
            cell.changed.connect(self.changed)

    def cells(self):
        if self.cell_sold is not None:
            return (("sold", self.cell_sold), ("rng", self.cell_rng))
        return (("spd", self.cell_spd), ("dmg", self.cell_dmg), ("rng", self.cell_rng))

    def any_on(self):
        return any(c.isChecked() for _, c in self.cells())

    def clear(self):
        for _, c in self.cells():
            c.setChecked(False)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.any_on()
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if on:
            g = QLinearGradient(0, 0, 0, self.height())
            g.setColorAt(0, QColor(178, 242, 82, 80))
            g.setColorAt(1, QColor(178, 242, 82, 20))
            p.setBrush(g)
            p.setPen(QPen(QColor("#111111"), 1))
        else:
            p.setBrush(QColor(0, 0, 0, 4))
            p.setPen(QPen(QColor(0, 0, 0, 12), 1))
        p.drawRoundedRect(r, 8, 8)


class InfoDialog(QDialog):
    """新粗野主义对话框：白底黑边 + 硬阴影，替代 QMessageBox。"""

    def __init__(self, parent, title, body):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        card = NeoCard(radius=12, shadow="#111111")
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(22, 18, 27, 23)
        v.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel(title)
        t.setFont(font(14, QFont.DemiBold))
        t.setStyleSheet("color:#111111;background:transparent;")
        head.addWidget(t)
        head.addStretch(1)
        x = QPushButton("\u2715")
        x.setFixedSize(24, 24)
        x.setStyleSheet("QPushButton{background:transparent;border:0;color:#8a8a8a;}"
                        "QPushButton:hover{color:#111111;background:#ffe500;border-radius:4px;}")
        x.clicked.connect(self.accept)
        head.addWidget(x)
        v.addLayout(head)

        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#111111;")
        v.addWidget(line)

        html_body = body.replace("\n", "<br>")
        txt = QLabel('<div style="line-height:150%%;color:#111111;">%s</div>' % html_body)
        txt.setWordWrap(True)
        txt.setFont(font(13))
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(txt)

        v.addSpacing(4)
        ok = QPushButton("知道了")
        ok.setObjectName("primary")
        ok.setFixedSize(96, 32)
        ok.setFont(font(11.5, QFont.DemiBold))
        ok.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        v.addLayout(row)

        self.adjustSize()
        self.setFixedWidth(440)

    def showEvent(self, e):
        super().showEvent(e)
        par = self.parentWidget()
        if par:
            g = par.window().geometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)


# ------------------------------------------------------------------ worker
class ConnectWorker(QThread):
    log = Signal(str)
    done = Signal(bool, str)

    def __init__(self, exe_path, wait_first):
        super().__init__()
        self.exe_path = exe_path
        self.wait_first = wait_first

    def run(self):
        if self.wait_first:
            self.log.emit("游戏未运行，正在启动…")
            try:
                self.launch()
            except Exception as e:
                self.done.emit(False, "启动失败: %s" % e)
                return
            pid = None
            for _ in range(90):
                pids = list_pids()
                if pids:
                    pid = pids[0]
                    break
                time.sleep(1.0)
            if not pid:
                self.done.emit(False, "等待游戏启动超时")
                return
            time.sleep(4.0)
        else:
            pids = list_pids()
            pid = pids[0] if pids else None
            if not pid:
                self.done.emit(False, "没有找到游戏进程")
                return

        ok, msg = inject_dll(pid, os.path.join(BRIDGE_DIR, "krbridge.dll"))
        self.done.emit(ok, "PID %d: %s" % (pid, msg))

    def launch(self):
        if not self.exe_path or not os.path.isfile(self.exe_path):
            raise FileNotFoundError(self.exe_path)
        if list_pids("steam.exe"):
            os.startfile("steam://rungameid/%s" % APPID)
            self.log.emit("已请求 Steam 启动游戏")
        else:
            subprocess.Popen([self.exe_path], cwd=os.path.dirname(self.exe_path))
            self.log.emit("已直接启动游戏进程")


# ------------------------------------------------------------------ app
class Trainer(QWidget):
    def __init__(self):
        super().__init__()
        self._grip = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setMinimumSize(700, 640)

        self.game_dir = detect_game_dir()
        self.bridge_ok = False
        self.worker = None
        self._ready = False
        self.tower_rows = {}
        self._tw_mtime = 0
        self._tw_names = ()
        self.payload_ver = ""
        self._sync_tries = 0
        self._sync_ts = 0.0

        os.makedirs(BRIDGE_DIR, exist_ok=True)
        self.prepare_assets()
        self.build()
        self._ready = True
        self.on_change()

        # 按内容自适应高度（不同分页内容高度不同，取最大的一页）
        scr = QApplication.primaryScreen().availableGeometry()
        need = max(self.sizeHint().height(), self.minimumSizeHint().height())
        self.resize(760, max(660, min(need, scr.height() - 80)))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(400)

    # -------------------------------------------------------------- assets
    def prepare_assets(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for name in ("krbridge.dll", "payload.lua"):
            src = os.path.join(base, name)
            dst = os.path.join(BRIDGE_DIR, name)
            try:
                if os.path.isfile(src) and (not os.path.isfile(dst) or
                                            os.path.getmtime(src) > os.path.getmtime(dst)):
                    shutil.copy(src, dst)
            except Exception:
                pass
        try:
            with open(os.path.join(BRIDGE_DIR, "payload.lua"), "r", encoding="utf-8",
                      errors="replace") as f:
                m = re.search(r'version\s*=\s*"([^"]+)"', f.read(4096))
                self.payload_ver = m.group(1) if m else ""
        except Exception:
            self.payload_ver = ""

    # -------------------------------------------------------------- paint
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))

    def resizeEvent(self, e):
        grip = getattr(self, "_grip", None)
        if grip is not None and hasattr(grip, "move"):
            grip.move(self.width() - 18, self.height() - 18)
        super().resizeEvent(e)

    # -------------------------------------------------------------- ui helpers
    def label(self, text, size=12, color=INK, spacing=0.0, weight=QFont.Normal):
        if has_cjk(text):
            spacing = 0.0          # 中文加字距会显得松散、边缘发虚
        lb = QLabel(text)
        lb.setFont(font(size, weight, spacing))
        lb.setStyleSheet("color:%s;background:transparent;" % color)
        return lb

    def card(self, num, title, hint="", extra=None):
        """建一张模块卡片，返回 (卡片, 内容布局)。"""
        card = NeoCard(radius=12)
        v = QVBoxLayout(card)
        # 右/下留出阴影偏移（4px）+ 描边内缩，避免内容压到边框
        v.setContentsMargins(14, 12, 18, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(9)
        head.addWidget(self.label(num, 11, INK_FAINT, 1.2, QFont.Bold))
        head.addWidget(self.label(title, 13, INK, 0, QFont.DemiBold))
        if hint:
            head.addWidget(self.label(hint, 11, INK_FAINT))
        head.addStretch(1)
        if extra is not None:
            head.addWidget(extra)
        v.addLayout(head)
        return card, v

    def shadow(self, btn, radius=8, dx=3, dy=3):
        """给按钮套上右下硬阴影（与卡片同款画法）。"""
        return ShadowWrap(btn, dx, dy, radius)

    def divider(self):
        d = QWidget()
        d.setFixedHeight(2)
        d.setStyleSheet("background:#111111;")
        return d

    def pad(self, content, l=14, r=12):
        """给页面内容统一左右内边距。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(l, 0, r, 0)
        v.setSpacing(0)
        v.addWidget(content)
        return box

    def func_row(self, title, hint, value=None, checked=False, lock_edit=True):
        row = FuncRow(title, hint, has_value=(value is not None), value=(value or ""),
                      checked=checked, lock_edit=lock_edit)
        row.changed.connect(self.on_change)
        return row

    # -------------------------------------------------------------- build
    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 自绘标题栏
        tb = TitleBar()
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(16, 0, 12, 0)
        tbl.setSpacing(7)
        tbl.addWidget(Mark())
        tbl.addWidget(self.label("KR", 15, "#111111", 2.1, QFont.DemiBold))
        tbl.addWidget(self.label("/", 13, "#8a8a8a"))
        tbl.addWidget(self.label("TRAINER", 12.5, "#111111", 2.6, QFont.Bold))
        tbl.addStretch(1)
        tbl.addWidget(self.label("v4 · 6.4.46 · STEAM", 11.5, "#555555", 0, QFont.DemiBold))
        tbl.addSpacing(10)
        for kind, fn in (("min", self.showMinimized), ("max", self.toggle_max),
                         ("close", self.close)):
            b = WinButton(kind)
            b.clicked.connect(fn)
            tbl.addWidget(b)
        root.addWidget(tb)

        # ---- 内容区
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(18, 14, 18, 10)
        lay.setSpacing(12)
        root.addWidget(body, 1)

        lay.addWidget(self.build_head())
        lay.addLayout(self.build_actions())

        # ---- 01 修改设置：三页签装在一个外框里
        card, v = self.card("01", "修改设置", "分页切换，改动即时生效")
        tabbar = QHBoxLayout()
        tabbar.setSpacing(6)
        self.tab_btns = []
        for i, name in enumerate(TABS):
            b = QPushButton(name)
            b.setObjectName("tab")
            b.setFixedHeight(32)
            b.setFont(font(12.5, QFont.Bold))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, x=i: self.switch_tab(x))
            tabbar.addWidget(self.shadow(b, radius=9, dx=3, dy=3), 1)
            self.tab_btns.append(b)
        v.addLayout(tabbar)
        v.addSpacing(8)

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        for page in (self.build_global(), self.build_towers(), self.build_hero()):
            self.stack.addWidget(self.wrap_page(page))
        v.addWidget(self.stack, 1)
        lay.addWidget(card, 1)

        # ---- 02 运行日志
        lay.addWidget(self.build_log())

        # 底栏
        foot = QHBoxLayout()
        foot.addWidget(self.label("只改内存，不动游戏文件 · 退出游戏自动还原", 11, INK_FAINT))
        foot.addStretch(1)
        foot.addWidget(self.label("适用于 Kingdom Rush 6.4.46 · Windows x64", 11, INK_FAINT))
        lay.addLayout(foot)

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.setStyleSheet("background:transparent;")
        self._grip.setToolTip("拖动调整窗口大小")
        self._grip.move(self.width() - 20, self.height() - 20)
        self._grip.raise_()

        self.switch_tab(0)
        self.load_towers(force=True)      # 建表不需要等定时器，也不依赖游戏在运行
        self.log("修改器已就绪，游戏目录: " + (self.game_dir or "未找到"))
        if not is_admin():
            self.log("提示: 当前非管理员权限，若游戏由 Steam 提权启动，注入会失败。")

    def wrap_page(self, card):
        """分页容器：内容页自己带左右内边距，这里只留上下间距。"""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 4, 0, 4)
        v.setSpacing(0)
        v.addWidget(card)
        return page

    def switch_tab(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.tab_btns):
            b.setProperty("on", i == idx)
            b.style().unpolish(b)
            b.style().polish(b)

    def build_head(self):
        blk = QWidget()
        fb = QVBoxLayout(blk)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(0)
        fb.addWidget(self.label("游戏程序", 11, INK_FAINT, 0, QFont.DemiBold))
        fb.addSpacing(8)

        line = QHBoxLayout()
        line.setSpacing(8)
        self.path_edit = QLineEdit(os.path.join(self.game_dir, GAME_EXE) if self.game_dir else "")
        self.path_edit.setObjectName("field")
        self.path_edit.setFixedHeight(36)
        self.path_edit.setFont(font(12))
        line.addWidget(self.path_edit, 1)
        browse = QPushButton("浏览")
        browse.setObjectName("glass")
        browse.setFixedSize(74, 36)
        browse.setFont(font(12))
        browse.clicked.connect(self.browse)
        line.addWidget(browse)
        fb.addLayout(line)
        fb.addSpacing(10)

        meta = QHBoxLayout()
        meta.setSpacing(6)
        self.proc_dot = Dot()
        meta.addWidget(self.proc_dot)
        self.proc_lbl = self.label("进程 未启动", 11.5, INK_DIM, 0, QFont.DemiBold)
        meta.addWidget(self.proc_lbl)
        meta.addSpacing(10)
        self.bridge_dot = Dot("#6B7280")
        meta.addWidget(self.bridge_dot)
        self.bridge_lbl = self.label("桥接 等待游戏启动", 11.5, INK_DIM, 0, QFont.DemiBold)
        meta.addWidget(self.bridge_lbl)
        meta.addStretch(1)
        self.readout_lbl = self.label("LEVEL - · 波次 - · 金币 - · 生命 -", 11.5,
                                      INK_FAINT, 0, QFont.DemiBold)
        meta.addWidget(self.readout_lbl)
        fb.addLayout(meta)
        return blk

    def build_actions(self):
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.connect_btn = QPushButton("启动 / 连接游戏")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setFixedHeight(34)
        self.connect_btn.setFont(font(12, QFont.Bold, 1.0))
        self.connect_btn.clicked.connect(self.connect)
        acts.addWidget(self.shadow(self.connect_btn), 14)
        for text, fn in (("Steam 说明", self.steam_help), ("恢复全部", self.restore_all)):
            b = QPushButton(text)
            b.setObjectName("glass")
            b.setFixedHeight(34)
            b.setFont(font(12, QFont.Bold))
            b.clicked.connect(fn)
            acts.addWidget(self.shadow(b), 10)
        return acts

    # ---- 分页 1：全局设置（原「金币」已并入，游戏速度置顶）
    def build_global(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        v.addWidget(self.pad(self.build_speed_row(), 0, 2))
        v.addSpacing(9)
        v.addWidget(self.pad(self.divider(), 14, 12))
        v.addSpacing(9)

        self.row_gold = self.func_row("锁定金币", "关卡内金币固定，买塔升级不扣钱", "9999")
        v.addWidget(self.pad(self.row_gold))
        v.addWidget(self.pad(self.build_kill_row()))
        self.row_gold_rate = self.func_row("每秒自动增加金币",
                                           "按设定数量持续产出，与锁定额度互不影响", "100")
        v.addWidget(self.pad(self.row_gold_rate))
        v.addSpacing(9)
        v.addWidget(self.pad(self.divider(), 14, 12))
        v.addSpacing(3)
        self.row_lives = self.func_row("锁定基地生命", "敌人漏过也不掉生命", "20")
        v.addWidget(self.pad(self.row_lives))
        self.row_auto = self.func_row("自动下一波", "每波结束立即自动开下一波，无需等待")
        v.addWidget(self.pad(self.row_auto))
        return page

    def build_speed_row(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 0, 12, 0)
        v.setSpacing(6)

        sr = QHBoxLayout()
        sr.setSpacing(6)
        self.speed_read = self.label("×1.0", 12.5, INK_FAINT, 0, QFont.Bold)
        sr.addWidget(self.label("游戏速度", 13, INK, 0, QFont.DemiBold))
        sr.addWidget(self.label("跳过动画与出兵间隔", 11, INK_FAINT))
        sr.addStretch(1)
        sr.addWidget(self.speed_read)
        v.addLayout(sr)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.speed_chips = {}
        for val in ("1", "2", "3", "5", "8"):
            b = QPushButton("%sx" % val)
            b.setObjectName("chip")
            b.setFixedSize(44, 28)
            b.setFont(font(12, QFont.Bold))
            b.setProperty("on", False)
            b.clicked.connect(lambda _, x=val: self.set_speed(x))
            chips.addWidget(self.shadow(b, dx=2, dy=2))
            self.speed_chips[val] = b
        chips.addSpacing(4)
        chips.addWidget(self.label("自定义", 11, INK_FAINT))
        self.speed_edit = QLineEdit("1.0")
        self.speed_edit.setObjectName("fieldSm")
        self.speed_edit.setFixedSize(64, 28)
        self.speed_edit.setFont(font(12, QFont.Bold))
        self.speed_edit.setAlignment(Qt.AlignCenter)
        self.speed_edit.textChanged.connect(self.on_change)
        chips.addWidget(self.speed_edit)
        chips.addStretch(1)
        v.addLayout(chips)
        return box

    def build_kill_row(self):
        box = QWidget()
        box.setFixedHeight(46)
        hm = QHBoxLayout(box)
        hm.setContentsMargins(14, 0, 12, 0)
        hm.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(self.label("杀敌金币倍率", 13, INK, 0, QFont.DemiBold))
        col.addWidget(self.label("击杀时额外发放「赏金 × 倍率」，0 为关闭", 11, INK_FAINT))
        hm.addLayout(col)
        hm.addStretch(1)
        self.kill_val = QLineEdit("0")
        self.kill_val.setObjectName("val")
        self.kill_val.setFixedSize(56, 30)
        self.kill_val.setFont(font(13, QFont.DemiBold))
        self.kill_val.setAlignment(Qt.AlignCenter)
        self.kill_val.textChanged.connect(self.on_change)
        hm.addWidget(self.kill_val)
        for val in ("2", "3", "5", "10"):
            b = QPushButton("%sx" % val)
            b.setObjectName("chipSm")
            b.setFixedSize(38, 26)
            b.setFont(font(10.5))
            b.clicked.connect(lambda _, x=val: self.kill_val.setText(x))
            hm.addWidget(self.shadow(b, radius=7, dx=2, dy=2))
        return box

    # ---- 分页 2：防御塔
    def build_towers(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.setContentsMargins(14, 0, 12, 0)
        bar.addWidget(self.label("按「系」放大属性，覆盖该系全部等级与进阶", 10.5, INK_FAINT))
        bar.addStretch(1)
        self.tw_status = self.label("", 11, INK_FAINT, 0, QFont.DemiBold)
        self.tw_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar.addWidget(self.tw_status)
        v.addLayout(bar)

        head = QHBoxLayout()
        head.setContentsMargins(14, 0, 12, 0)
        head.setSpacing(8)
        head.addSpacing(12)
        lb = self.label("塔系", 11, INK_FAINT, 0, QFont.Bold)
        lb.setFixedWidth(70)
        head.addWidget(lb)
        head.addStretch(1)
        tips = {
            "攻速": "受开火动画限制：倍数超过「cd ÷ 开火时长」后不再变快\n"
                    "弓兵≈4.8x（神射手/游侠≈3x）、法师≈5.6x（魔法师≈4.1x、奥术≈3x）\n"
                    "火炮≈7.5x（大贝莎≈4.8x、特斯拉≈1.4x）、火枪手≈7.5x",
            "伤害": "直接乘在弹药模板的伤害上下限上，无上限",
            "范围": "射程倍数；兵营系对应驻扎半径",
        }
        for text in ("攻速", "伤害", "范围"):
            lb = self.label(text, 11, INK_FAINT, 0, QFont.Bold)
            lb.setFixedWidth(TW_COL_W)
            lb.setAlignment(Qt.AlignCenter)
            lb.setToolTip(tips.get(text, ""))
            head.addWidget(lb)
        v.addLayout(head)

        self.tw_box = QVBoxLayout()
        self.tw_box.setSpacing(4)
        self.tw_box.setContentsMargins(14, 0, 12, 0)
        v.addLayout(self.tw_box)
        self.tw_empty = self.label("正在读取塔数据…", 11.5, INK_FAINT)
        self.tw_box.addWidget(self.tw_empty)
        hint = self.label("倍数 1 = 不变，取消勾选即还原；兵营系不攻击，改调「士兵数量」与「范围」",
                          10.5, INK_FAINT)
        hint.setContentsMargins(14, 2, 12, 0)
        v.addWidget(hint)
        return page

    # ---- 分页 3：英雄与友军
    def build_hero(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.row_heal = self.func_row("英雄与友军每帧回满血",
                                      "英雄、援军、兵营单位血量始终满格")
        self.row_hero = self.func_row("英雄升级到满级", "勾选瞬间拉满等级，血量/护甲/攻击同步到最高档")
        self.row_nocd = self.func_row("技能与援军无冷却", "火雨与援军可连续释放，无需等待")
        self.row_reinf = self.func_row("援军总数", "每次呼叫后的士兵总数（基础 2，最多 10）", "4")
        self.row_reinf.edit.setValidator(QIntValidator(2, 10, self.row_reinf))
        self.row_reinf_time = self.func_row("援军持续时间",
                                            "援军在场上的停留秒数（游戏默认 20，最多 300）", "60")
        self.row_reinf_time.edit.setValidator(QIntValidator(5, 300, self.row_reinf_time))
        for r in (self.row_heal, self.row_hero, self.row_nocd, self.row_reinf, self.row_reinf_time):
            v.addWidget(self.pad(r))
        return page

    # ---- 02 运行日志
    def build_log(self):
        card, v = self.card("02", "运行日志", "注入与改动记录")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(font(11))
        self.log_box.setFixedHeight(96)
        self.log_box.setStyleSheet(
            "QTextEdit{background:transparent;border:0;color:#333333;"
            "selection-background-color:#ffe500;}")
        v.addWidget(self.log_box)
        return card

    # -------------------------------------------------------------- towers
    @staticmethod
    def _parse_towers(text):
        rows = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 10:
                continue
            try:
                rows.append((
                    parts[0], parts[1], parts[2], int(parts[3]), float(parts[4]),
                    float(parts[5]), float(parts[6]), float(parts[7]), float(parts[8]),
                    parts[10] if len(parts) > 10 else "",
                    int(float(parts[11])) if len(parts) > 11 else 0,
                ))
            except ValueError:
                continue
        return rows

    def load_towers(self, force=False):
        """优先用 payload 扫描出的 towers.txt；没有就用内置表，界面无需连游戏也能建表。"""
        path = os.path.join(BRIDGE_DIR, "towers.txt")
        stamp = -1
        text = None
        if os.path.isfile(path):
            try:
                stamp = os.path.getmtime(path)
                if not force and stamp == self._tw_mtime:
                    return
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                text = None
        elif not force and self._tw_mtime == -1:
            return

        rows = self._parse_towers(text)
        if rows:
            label = "已获取 %d 种 / %d 系" % (len(rows), len(FAMILIES))
        else:
            rows = self._parse_towers(TOWERS_BUILTIN)
            label = "内置数据 · %d 种" % len(rows)
        if not rows:
            return

        self._tw_mtime = stamp
        self.tw_status.setText(label)
        names = tuple(rows)      # 整行参与比较：士兵数这类新增列变化也会触发重建
        if names == self._tw_names:
            return
        self._tw_names = names
        self.rebuild_towers(rows)

    def rebuild_towers(self, rows):
        """把 22 种塔按「系」聚合，只生成 4 行；倍数作用于该系全部等级与进阶。"""
        groups = {}
        for tpl, zh, typ, level, price, cd, dmin, dmax, rng, fam, soldiers in rows:
            key = fam or TYPE_FAMILY.get(typ, "")
            if key not in FAMILY_INFO:
                continue                     # 特殊塔（如 Sasquash）不入表
            g = groups.setdefault(key, {"members": [], "base": None})
            g["members"].append((level, zh, cd, dmin, dmax, rng))
            if g["base"] is None or level < g["base"][0]:
                g["base"] = (level, zh, cd, dmin, dmax, rng, soldiers)

        old = {key: [(k, c.isChecked(), c.value()) for k, c in r.cells()]
               for key, r in self.tower_rows.items()}
        self.tower_rows = {}
        while self.tw_box.count():
            item = self.tw_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        for key, _, color in FAMILIES:
            g = groups.get(key)
            if not g:
                continue
            name, _c = FAMILY_INFO[key]
            level, _zh, cd, dmin, dmax, rng, soldiers = g["base"]
            if cd > 0:
                stat = "L%d %.2fs %d-%d 射%d" % (level, cd, dmin, dmax, rng)
            elif soldiers > 0:
                stat = "L%d 射%d · 兵%d" % (level, rng, soldiers)
            else:
                stat = "L%d 射%d" % (level, rng)
            info = "%d 种 · %s" % (len(g["members"]), stat)
            tip = "%s 共 %d 种：\n%s" % (
                name, len(g["members"]),
                "\n".join("· %s" % m[1] for m in sorted(g["members"])))
            row = TowerRow(key, name, info, cd > 0, tip, color,
                           barrack=(key == "barrack"), soldiers=soldiers)
            row.changed.connect(self.on_change)
            for k, on, val in old.get(key, ()):
                for ck, c in row.cells():
                    if ck == k and c.enabled:
                        c.setValue(val)
                        c.setChecked(on)
            self.tower_rows[key] = row
            self.tw_box.addWidget(row)
        self.on_change()

    # -------------------------------------------------------------- actions
    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 Kingdom Rush.exe", "", "可执行文件 (*.exe)")
        if p:
            self.path_edit.setText(p)
            self.game_dir = os.path.dirname(p)

    def set_speed(self, v):
        self.speed_edit.setText(v)
        self.on_change()

    def current_speed(self):
        try:
            v = float(self.speed_edit.text() or "1")
        except ValueError:
            v = 1.0
        return max(0.1, min(20.0, v))

    def on_change(self):
        if not self._ready:
            return
        sp = self.current_speed()
        for val, btn in self.speed_chips.items():
            btn.setProperty("on", abs(sp - float(val)) < 0.01)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.speed_read.setText("×%.1f" % sp)
        self.write_state()

    def restore_all(self):
        for row in (self.row_gold, self.row_gold_rate, self.row_lives,
                    self.row_heal, self.row_hero, self.row_nocd, self.row_reinf, self.row_reinf_time,
                    self.row_auto):
            row.setChecked(False)
        for r in self.tower_rows.values():
            r.clear()
        self.speed_edit.setText("1.0")
        self.kill_val.setText("0")
        self.on_change()
        self.log("已恢复全部修改")

    def steam_help(self):
        InfoDialog(self, "Steam 启动说明",
                   "1.  游戏没开时点「启动 / 连接游戏」，会通过 Steam 拉起并自动注入。\n"
                   "2.  Steam 没响应时，直接双击游戏目录里的 Kingdom Rush.exe。\n"
                   "3.  游戏已在运行时，再点一次按钮即可连接。\n"
                   "4.  必须以管理员身份运行，否则注入会失败（错误码 5）。\n"
                   "5.  塔表参数为内置数据，连接游戏后会自动同步一次，无需手动操作。").exec()

    # -------------------------------------------------------------- state
    @staticmethod
    def _mult(cell):
        if not cell.isChecked():
            return "0"
        try:
            v = float(cell.value())
        except ValueError:
            v = 1.0
        return "%g" % max(0.0, min(100.0, v))

    def write_state(self):
        if not self._ready:
            return
        vals = [
            ("gold_lock", 1 if self.row_gold.isChecked() else 0),
            ("gold_value", self.row_gold.value() or "0"),
            ("kill_mult", self.kill_val.text() or "0"),
            ("gold_rate", self.row_gold_rate.value() if self.row_gold_rate.isChecked() else "0"),
            ("lives_lock", 1 if self.row_lives.isChecked() else 0),
            ("lives_value", self.row_lives.value() or "0"),
            ("heal_friend", 1 if self.row_heal.isChecked() else 0),
            ("hero_max", 1 if self.row_hero.isChecked() else 0),
            ("nocd", 1 if self.row_nocd.isChecked() else 0),
            ("reinf_add", self.row_reinf.value() if self.row_reinf.isChecked() else "0"),
            ("reinf_time", self.row_reinf_time.value() if self.row_reinf_time.isChecked() else "0"),
            ("speed", self.current_speed()),
            ("auto_wave", 1 if self.row_auto.isChecked() else 0),
        ]
        for tpl, row in self.tower_rows.items():
            for key, cell in row.cells():
                vals.append(("tw_%s_%s" % (tpl, key), self._mult(cell)))
        try:
            with open(os.path.join(BRIDGE_DIR, "state.txt"), "w", encoding="utf-8") as f:
                for k, v in vals:
                    f.write("%s=%s\n" % (k, v))
        except Exception as e:
            self.log("写入配置失败: %s" % e)

    # -------------------------------------------------------------- connect
    def connect(self):
        if self.worker and self.worker.isRunning():
            return
        pids = list_pids()
        exe = self.path_edit.text()
        self.worker = ConnectWorker(exe, not pids)
        self.worker.log.connect(self.log)
        self.worker.done.connect(self.on_connected)
        self.connect_btn.setEnabled(False)
        self.log("正在连接游戏…" if pids else "游戏未运行，准备启动…")
        self.worker.start()

    def on_connected(self, ok, msg):
        self.connect_btn.setEnabled(True)
        if ok:
            self.bridge_ok = True
            self.log("注入成功 (%s)，等待桥接就绪…" % msg)
        else:
            self.bridge_ok = False
            self.log("注入失败: %s" % msg)
            if not is_admin():
                self.log("→ 请以管理员身份运行修改器后重试")

    # -------------------------------------------------------------- loop
    def tick(self):
        pids = list_pids()
        if pids:
            self.proc_lbl.setText("进程 %d" % pids[0])
            self.proc_dot.setColor(GOLD)
        else:
            self.proc_lbl.setText("进程 未启动")
            self.proc_dot.setColor("#6B7280")
            self.bridge_ok = False

        raw = ""
        fresh = False
        p = os.path.join(BRIDGE_DIR, "status.txt")
        if os.path.isfile(p):
            try:
                fresh = (time.time() - os.path.getmtime(p)) < 3.0
                if fresh:
                    raw = open(p, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                pass
        st = {}
        for line in raw.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                st[k] = v

        if pids and fresh and st and not self.bridge_ok:
            self.bridge_ok = True
            self.log("桥接已就绪，游戏内脚本运行中")

        if self.bridge_ok:
            self.bridge_lbl.setText("桥接 已连接")
            self.bridge_dot.setColor(GOLD)
        elif pids:
            self.bridge_lbl.setText("桥接 等待连接")
            self.bridge_dot.setColor("#C9A227")
        else:
            self.bridge_lbl.setText("桥接 等待游戏启动")
            self.bridge_dot.setColor("#6B7280")

        if st.get("state") == "ingame":
            self.readout_lbl.setText("LEVEL %s · %s 波 · 金币 %s · 生命 %s" %
                                     (st.get("level", "-"), st.get("wave", "-"),
                                      st.get("gold", "-"), st.get("lives", "-")))
        else:
            self.readout_lbl.setText("LEVEL - · 波次 - · 金币 - · 生命 -")

        self.sync_payload(st if (pids and fresh) else None)
        self.load_towers()

    def sync_payload(self, st):
        """游戏里跑的脚本比 exe 旧时，用调试通道让它就地重载（否则得重启游戏才生效）。"""
        if not st or not self.payload_ver:
            return
        cur = st.get("v")
        if not cur or cur == self.payload_ver:
            self._sync_tries = 0
            self.drop_sync_cmd()
            return
        now = time.time()
        if self._sync_tries >= 5 or now - self._sync_ts < 4.0:
            return
        self._sync_tries += 1
        self._sync_ts = now
        try:
            # 末尾带时间戳：载荷按「内容是否变化」判断要不要执行，内容不重复才会再触发
            with open(os.path.join(BRIDGE_DIR, "cmd.txt"), "w", encoding="utf-8") as f:
                f.write("%s\n-- %d" % (PAYLOAD_SYNC, int(now * 1000)))
            self.log("游戏内脚本 v%s 较旧 → 重载到 v%s" % (cur, self.payload_ver))
        except Exception as e:
            self.log("重载脚本失败: %s" % e)

    @staticmethod
    def drop_sync_cmd():
        """重载成功后清掉自己写的调试命令，别把 cmd.txt 留在游戏目录里。"""
        path = os.path.join(BRIDGE_DIR, "cmd.txt")
        try:
            if not os.path.isfile(path):
                return
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()          # 先读完关掉句柄，Windows 下打开着的文件删不掉
            if txt.startswith(PAYLOAD_SYNC):
                os.remove(path)
        except Exception:
            pass

    def log(self, msg):
        cur = self.log_box.textCursor()
        cur.movePosition(QTextCursor.End)
        ft = QTextCharFormat()
        ft.setForeground(QColor("#8a8a8a"))
        fm = QTextCharFormat()
        fm.setForeground(QColor("#333333"))
        cur.insertText(time.strftime("%H:%M:%S"), ft)
        cur.insertText("  " + str(msg) + "\n", fm)
        self.log_box.setTextCursor(cur)
        self.log_box.ensureCursorVisible()


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(BG))
    p.setPen(QPen(QColor("#111111"), 3))
    p.drawRoundedRect(QRectF(3, 3, 58, 58), 14, 14)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(PINK))
    p.drawRoundedRect(QRectF(20, 20, 24, 24), 6, 6)
    p.end()
    return QIcon(pm)


def build_qss():
    return """
QWidget { color: #111111; }

QLineEdit#field {
  background: #ffffff; border: 3px solid #111111;
  border-radius: 8px; padding-left: 12px; padding-right: 12px;
  color: #111111; selection-background-color: #ffe500;
}
QLineEdit#field:focus { background: #fffbe6; }

QLineEdit#fieldSm {
  background: #ffffff; border: 3px solid #111111;
  border-radius: 8px; color: #111111; font-weight: 800;
  selection-background-color: #ffe500;
}

QLineEdit#val {
  background: #ffffff; border: 3px solid #111111;
  border-radius: 8px; padding-right: 12px; padding-left: 10px; color: #111111;
  selection-background-color: #ffe500;
}
QLineEdit#val[on="true"] { background: #fffbe6; }
QLineEdit#val:focus { background: #fffbe6; }
QLineEdit#val:disabled { color: #999999; border-color: #cccccc; background: #eeeeee; }

QLineEdit#tw {
  background: #ffffff; border: 2px solid #111111;
  border-radius: 6px; color: #111111; font-weight: 700;
  selection-background-color: #ffe500;
}
QLineEdit#tw:focus { background: #fffbe6; }
QLineEdit#tw:disabled { color: #bbbbbb; border-color: #cccccc; background: #f2f2f2; }

QPushButton#glass {
  background: #ffffff; border: 3px solid #111111; border-radius: 8px; color: #111111; font-weight: 700;
}
QPushButton#glass:hover { background: #ffe500; }
QPushButton#glass:pressed { background: #f0d800; }

QPushButton#primary {
  background: #ff6bcb; border: 3px solid #111111; border-radius: 8px;
  color: #111111; font-weight: 700;
}
QPushButton#primary:hover { background: #ff8fd8; }
QPushButton#primary:pressed { background: #f05ab8; }
QPushButton#primary:disabled { background: #eeeeee; color: #999999; border-color: #cccccc; }

QPushButton#quiet {
  background: #b2f252; border: 3px solid #111111; border-radius: 8px;
  color: #111111; font-weight: 700;
}
QPushButton#quiet:hover { background: #c4f97a; }

QPushButton#chip {
  background: #ffffff; border: 3px solid #111111; border-radius: 8px; color: #111111; font-weight: 700;
}
QPushButton#chip:hover { background: #ffe500; }
QPushButton#chip[on="true"] { background: #ffe500; }

QPushButton#chipSm {
  background: #ffffff; border: 3px solid #111111; border-radius: 7px; color: #111111; font-weight: 700;
}
QPushButton#chipSm:hover { background: #ffe500; }

QPushButton#tab {
  background: #ffffff; border: 3px solid #111111; border-radius: 9px; color: #555555; font-weight: 800;
}
QPushButton#tab:hover { background: #fffbe6; color: #111111; }
QPushButton#tab[on="true"] { background: #ffe500; color: #111111; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
  background: #111111; border-radius: 4px; min-height: 28px;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip { background: #111111; color: #f5f0e8; border: 2px solid #111111; }
"""


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(build_qss())
    win = Trainer()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
