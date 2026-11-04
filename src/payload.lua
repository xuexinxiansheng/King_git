--[[
  Kingdom Rush 修改器 - 游戏内载荷 (payload)
  由 krbridge.dll 注入到游戏 Lua 状态后执行，负责：
    * 读取 %LOCALAPPDATA%\KR_Trainer\state.txt 中的开关
    * 每帧应用修改
    * 把实时状态写回 status.txt、防御塔清单写回 towers.txt

  四大模块：
    01 金币    gold_lock / kill_mult / gold_rate
    02 防御塔  扫描 towers.txt + tw_<模板>_spd/_dmg/_rng
    03 英雄     heal_friend / hero_max / nocd / reinf_add
    04 全局     speed / auto_wave / lives_lock

  游戏数据模型（逆向结论，勿凭猜）：
    塔实体   e.template_name / e.tower.type / e.tower.level / e.tower.price
             e.attacks.range                      射程
             e.attacks.list[i].cooldown           攻击间隔（越小越快）
             e.attacks.list[i].bullet             弹药模板名
             e.barrack.rally_range                兵营驻扎半径
    弹药模板 tpl.bullet.damage_min/max           普通子弹
             tpl.bounce_damage_min/max            连锁闪电
             tpl.mod -> mod_xxx.dps.damage_min/max 持续伤害类
    英雄     e.hero.level/xp/level_stats/fn_level_up
--]]

local BASE = (os.getenv("LOCALAPPDATA") or ".") .. "\\KR_Trainer\\"
local STATE = BASE .. "state.txt"
local STATUS = BASE .. "status.txt"
local TOWERS = BASE .. "towers.txt"
local LOGF = BASE .. "payload.log"

local function readfile(p)
  local h = io.open(p, "rb")
  if not h then return nil end
  local s = h:read("*a")
  h:close()
  return s
end

local function writefile(p, s)
  local h = io.open(p, "wb")
  if not h then return false end
  h:write(s)
  h:close()
  return true
end

local function log(...)
  local t = {}
  for i = 1, select("#", ...) do t[i] = tostring((select(i, ...))) end
  local h = io.open(LOGF, "ab")
  if h then h:write(table.concat(t, " "), "\n") h:close() end
end

_G.__KRT_LOADS = (_G.__KRT_LOADS or 0) + 1
-- 每次载入递增代号：旧实例的钩子会立刻变成空转，避免热重载后两套钩子互相包装
_G.__KRT_GEN = (_G.__KRT_GEN or 0) + 1
local GEN = _G.__KRT_GEN

local KRT = {
  version = "2.0",
  cfg = {},
  poll = 0,
  speed = 1.0,
  counted = {},
  level_key = nil,
  tick_acc = 0,
  gold_acc = 0,
  tw_touched = setmetatable({}, { __mode = "k" }),
  dmg_base = setmetatable({}, { __mode = "k" }),
  trk_base = setmetatable({}, { __mode = "k" }),
  tw_count = 0,
}
_G.__KRT = KRT

-- ---------------------------------------------------------------- state file
local function parse_state(src)
  local cfg = {}
  for key, val in src:gmatch("([%w_]+)%s*=%s*([^%s\r\n]+)") do
    cfg[key] = tonumber(val) or val
  end
  return cfg
end

local function load_state()
  local src = readfile(STATE)
  if not src then return end
  if src == KRT.last_state then return end
  KRT.last_state = src
  KRT.cfg = parse_state(src)
  KRT.speed = tonumber(KRT.cfg.speed) or 1.0
  if KRT.speed <= 0 then KRT.speed = 1.0 end
end

-- ---------------------------------------------------------------- helpers
local function active_game()
  local ok, director = pcall(require, "director")
  if not ok or type(director) ~= "table" then return nil end
  local item = director.active_item
  if type(item) == "table" and item.store and item.store.entities then
    return item
  end
  return nil
end
KRT.active_game = active_game

local function edb_entities()
  local ok, edb = pcall(require, "entity_db")
  if not ok or type(edb) ~= "table" then return nil end
  local ents = edb.entities
  if type(ents) == "table" and next(ents) then return ents, edb end
  return nil, edb
end

local function rnd(v)
  if v >= 0 then return math.floor(v + 0.5) end
  return -math.floor(-v + 0.5)
end

-- ================================================================ 01 金币
local function kill_bonus(store)
  local mult = tonumber(KRT.cfg.kill_mult) or 0
  if mult <= 0 then return end
  for id, e in pairs(store.entities) do
    local h = e.health
    if e.enemy and h and h.dead and not KRT.counted[id] then
      KRT.counted[id] = true
      local bounty = (e.enemy.gold or 0) + (e.enemy.gems or 0) * 0
      if bounty > 0 then
        store.player_gold = (store.player_gold or 0) + math.floor(bounty * mult + 0.5)
      end
    end
  end
end

local function apply_gold(store, dt)
  local cfg = KRT.cfg
  if cfg.gold_lock == 1 then
    store.player_gold = math.floor(tonumber(cfg.gold_value) or 0)
  end
  local rate = tonumber(cfg.gold_rate) or 0
  if rate > 0 then
    KRT.gold_acc = (KRT.gold_acc or 0) + rate * dt
    local add = math.floor(KRT.gold_acc)
    if add >= 1 then
      KRT.gold_acc = KRT.gold_acc - add
      store.player_gold = (store.player_gold or 0) + add
    end
  else
    KRT.gold_acc = 0
  end
end

-- ================================================================ 02 防御塔
local NAME_KEY = {
  tower_ranger = "TOWER_RANGERS_NAME",
  tower_musketeer = "TOWER_MUSKETEERS_NAME",
  tower_paladin = "TOWER_PALADINS_NAME",
  tower_barbarian = "TOWER_BARBARIANS_NAME",
  tower_sorcerer = "TOWER_SORCERER_NAME",
  tower_arcane_wizard = "TOWER_ARCANE_NAME",
  tower_tesla = "TOWER_TESLA_NAME",
  tower_bfg = "TOWER_BFG_NAME",
  tower_elf = "TOWER_ELF_NAME",
  tower_sasquash = "TOWER_SASQUASH_NAME",
}
local NAME_FALLBACK = {
  tower_elf = "精灵塔",
  tower_sasquash = "Sasquash",
}
local SKIP_TPL = {
  tower_build = true, tower_build_archer = true, tower_build_barrack = true,
  tower_build_engineer = true, tower_build_mage = true,
  tower_holder = true, tower_holder_grass = true, tower_holder_snow = true,
  tower_holder_wasteland = true, tower_holder_blackburn = true,
  tower_elf_holder = true, tower_sasquash_holder = true,
  tower_sunray = true, tower_sunray_layer = true, tower_buff = true,
  tower_preview_archer = true, tower_preview_mage = true,
  tower_preview_barrack = true, tower_preview_artillery = true,
}
local FAMILY = { archer = 1, mage = 2, barrack = 3, engineer = 4 }
-- 塔型 → 系（4 级进阶与分支并入原系，界面上只显示 4 行）
local FAMILY_OF = {
  archer = "archer", ranger = "archer", musketeer = "archer",
  mage = "mage", sorcerer = "mage", arcane_wizard = "mage",
  barrack = "barrack", paladin = "barrack", barbarian = "barrack", elf = "barrack",
  engineer = "engineer", tesla = "engineer", bfg = "engineer",
}

local function zh_name(tpl_name)
  local key = NAME_KEY[tpl_name]
  if not key then
    key = "TOWER_" .. tpl_name:gsub("^tower_", ""):upper() .. "_NAME"
  end
  local ok, i18n = pcall(require, "i18n")
  local loc = ok and type(i18n) == "table" and i18n.current_locale or "en"
  local ok2, sl = pcall(require, "strings." .. loc)
  if ok2 and type(sl) == "table" and type(sl[key]) == "string" then return sl[key] end
  return NAME_FALLBACK[tpl_name] or tpl_name
end

-- 找到持有伤害数值的子表，返回 (表, 最小键, 最大键)
local function damage_holder(bp, ents)
  if type(bp) ~= "table" then return nil end
  if type(bp.bullet) == "table" and (bp.bullet.damage_max or 0) > 0 then
    return bp.bullet, "damage_min", "damage_max"
  end
  if (bp.bounce_damage_max or 0) > 0 then
    return bp, "bounce_damage_min", "bounce_damage_max"
  end
  -- 持续伤害类（奥术射线等）：伤害挂在 bullet.mod 指向的 dps 表上
  local mod = bp.mod
  if type(bp.bullet) == "table" then mod = mod or bp.bullet.mod end
  if type(mod) == "string" then
    local m = ents and ents[mod]
    if type(m) == "table" and type(m.dps) == "table" then
      return m.dps, "damage_min", "damage_max"
    end
  elseif type(mod) == "table" and type(mod.dps) == "table" then
    return mod.dps, "damage_min", "damage_max"
  end
  for _, v in pairs(bp) do
    if type(v) == "table" and type(v.damage_max) == "number" and v.damage_max > 0 then
      return v, "damage_min", "damage_max"
    end
  end
end

-- 弹药的跟踪距离（共享模板，按基准值覆写，避免反复乘累积）
-- 注意：ta.list[i].bullet 是模板名字符串（如 "bomb"），要先解析成模板
local function patch_track(bullet, mult, ents)
  if type(bullet) ~= "string" or bullet == "" then return end
  local bp = ents and ents[bullet]
  if type(bp) ~= "table" then return end
  local holder = (type(bp.bullet) == "table") and bp.bullet or bp
  if type(holder.max_track_distance) ~= "number" or holder.max_track_distance <= 0 then return end
  local base = KRT.trk_base[holder]
  if not base then
    base = holder.max_track_distance
    KRT.trk_base[holder] = base
  end
  holder.max_track_distance = base * (mult > 0 and mult or 1)
end

local function patch_damage(bullet, mult, ents)
  if type(bullet) ~= "string" or bullet == "" then return end
  local holder, kmin, kmax = damage_holder(ents and ents[bullet], ents)
  if not holder then return end
  local base = KRT.dmg_base[holder]
  if not base then
    base = { min = holder[kmin] or 0, max = holder[kmax] or 0 }
    KRT.dmg_base[holder] = base
  end
  holder[kmin] = rnd(base.min * mult)
  holder[kmax] = rnd(base.max * mult)
end

local function tower_mults()
  local cfg = KRT.cfg
  if KRT._mult_cfg == cfg then return KRT._mult end
  local out = {}
  for k, v in pairs(cfg) do
    -- Lua 模式没有 | 交替，这里用三段字母再校验
    local name, field = tostring(k):match("^tw_(.+)_([a-z][a-z][a-z])$")
    if name and (field == "spd" or field == "dmg" or field == "rng") then
      local t = out[name]
      if not t then t = {} out[name] = t end
      t[field] = tonumber(v) or 0
    end
  end
  KRT._mult_cfg, KRT._mult = cfg, out
  return out
end

-- 用模板原型做基准，每帧覆写；关闭时自动还原，不会累积放大
local function apply_tower(e, tpl, spd, dmg, rng, ents)
  if type(tpl) ~= "table" then return end
  local ea, ta = e.attacks, tpl.attacks
  if ea and ta then
    if type(ea.range) == "number" and type(ta.range) == "number" then
      ea.range = (rng > 0) and ta.range * rng or ta.range
    end
    -- 每种攻击模式可能自带射程（如 500mm大贝莎：炸弹 range=199、集束炸弹 range=180、
    -- 导弹 range_base=199），只放大 attacks.range 的话远目标照样不打
    if type(ea.list) == "table" and type(ta.list) == "table" then
      local m = (rng > 0) and rng or 1
      for i = 1, #ta.list do
        local a, b = ea.list[i], ta.list[i]
        if type(a) == "table" and type(b) == "table" then
          for _, key in ipairs({"range", "range_base"}) do
            if type(b[key]) == "number" then
              a[key] = b[key] * m
            end
          end
          -- 跟踪距离：炮弹/子弹只在该半径内修正弹道，不放大远距离打不中
          if type(b.max_track_distance) == "number" and b.max_track_distance > 0 then
            a.max_track_distance = b.max_track_distance * m
          end
        end
      end
    end
    if spd > 0 and type(ea.list) == "table" and type(ta.list) == "table" then
      for i = 1, #ta.list do
        local a, b = ea.list[i], ta.list[i]
        if type(a) == "table" and type(b) == "table" and type(b.cooldown) == "number" then
          a.cooldown = b.cooldown / spd
        end
      end
    elseif spd <= 0 and type(ea.list) == "table" and type(ta.list) == "table" then
      for i = 1, #ta.list do
        local a, b = ea.list[i], ta.list[i]
        if type(a) == "table" and type(b) == "table" and type(b.cooldown) == "number" then
          a.cooldown = b.cooldown
        end
      end
    end
    if type(ta.list) == "table" then
      for i = 1, #ta.list do
        local bullet = ta.list[i].bullet
        patch_damage(bullet, (dmg > 0) and dmg or 1, ents)
        patch_track(bullet, (rng > 0) and rng or 1, ents)
      end
    end
  end
  local eb, tb = e.barrack, tpl.barrack
  if eb and tb and type(eb.rally_range) == "number" and type(tb.rally_range) == "number" then
    eb.rally_range = (rng > 0) and tb.rally_range * rng or tb.rally_range
  end
end

local function apply_towers(store)
  local ents = edb_entities()
  local mults = tower_mults()
  for _, e in pairs(store.entities) do
    local tw = e.tower
    local name = e.template_name
    if tw and type(name) == "string" and name:find("^tower_") then
      local m = FAMILY_OF[tw.type] and mults[FAMILY_OF[tw.type]]
      local spd = m and m.spd or 0
      local dmg = m and m.dmg or 0
      local rng = m and m.rng or 0
      if spd > 0 or dmg > 0 or rng > 0 then
        KRT.tw_touched[e] = true
        apply_tower(e, ents and ents[name], spd, dmg, rng, ents)
      elseif KRT.tw_touched[e] then
        KRT.tw_touched[e] = nil
        apply_tower(e, ents and ents[name], 0, 0, 0, ents)
      end
    end
  end
end

-- 扫描所有防御塔模板，写入 towers.txt
local function scan_towers()
  local ents, edb = edb_entities()
  if not ents then
    -- 主菜单没有关卡，模板未注册：手动加载一次（会重置实体表，故仅在无游戏时做）
    if active_game() then return false end
    local ok = pcall(function() edb:load() end)
    if not ok then return false end
    ents = select(1, edb_entities())
    if not ents then return false end
  end

  local list = {}
  for name, tpl in pairs(ents) do
    if type(name) == "string" and name:find("^tower_") and not SKIP_TPL[name]
        and type(tpl) == "table" and type(tpl.tower) == "table"
        and type(tpl.tower.type) == "string" and type(tpl.tower.level) == "number" then
      local a = tpl.attacks
      local atk = a and a.list and a.list[1]
      local bullet = type(atk) == "table" and atk.bullet or ""
      local cd = type(atk) == "table" and atk.cooldown or 0
      local dmin, dmax = 0, 0
      if bullet ~= "" then
        local holder, kmin, kmax = damage_holder(ents[bullet], ents)
        if holder then dmin, dmax = holder[kmin] or 0, holder[kmax] or 0 end
      end
      local range = 0
      if a and type(a.range) == "number" then
        range = a.range
      elseif type(tpl.barrack) == "table" and type(tpl.barrack.rally_range) == "number" then
        range = tpl.barrack.rally_range
      end
      list[#list + 1] = {
        name = name,
        zh = zh_name(name),
        type = tpl.tower.type,
        level = tpl.tower.level,
        price = tpl.tower.price or 0,
        cd = cd,
        dmin = dmin,
        dmax = dmax,
        range = range,
        bullet = bullet,
        order = (FAMILY[tpl.tower.type] or 9) * 100 + (tpl.tower.level or 0),
      }
    end
  end
  table.sort(list, function(a, b)
    if a.order ~= b.order then return a.order < b.order end
    return a.name < b.name
  end)

  local lines = { "# tpl|zh|type|level|price|cd|dmg_min|dmg_max|range|bullet|family" }
  for _, t in ipairs(list) do
    lines[#lines + 1] = table.concat({
      t.name, t.zh, t.type, tostring(t.level), tostring(t.price),
      string.format("%.3f", t.cd), string.format("%.2f", t.dmin),
      string.format("%.2f", t.dmax), string.format("%.2f", t.range), t.bullet,
      FAMILY_OF[t.type] or "",
    }, "|")
  end
  writefile(TOWERS, table.concat(lines, "\n"))
  KRT.tw_count = #list
  log("towers scanned: " .. #list)
  return true
end

-- ================================================================ 03 英雄与友军
local function heal_friendlies(store)
  for _, e in pairs(store.entities) do
    local h = e.health
    if h and not e.enemy and not h.dead then
      local mx = h.hp_max or 0
      if mx > 0 and (h.hp or 0) < mx then
        h.hp = mx
      end
    end
  end
end

-- 英雄满级：直接把 level 提到最高档，再调英雄自己的 fn_level_up(entity) 应用该档属性。
-- 只补 xp 是不行的——游戏每次获得经验才升 1 级（表现为「打一下升一级」），
-- 而 fn_level_up 只负责按当前 level 写属性，所以先改 level 再调用即可瞬间满级。
local function hero_top_level(hero)
  local ls = hero and hero.level_stats
  local t = type(ls) == "table" and ls.hp_max
  if type(t) ~= "table" then return 10 end
  return #t
end

local function apply_hero_max(store)
  if KRT.cfg.hero_max ~= 1 then return end
  for _, e in pairs(store.entities) do
    local hero, h = e.hero, e.health
    -- 死亡中的英雄先不管：等复活后再拉满，避免打断死亡/复活流程
    if type(hero) == "table" and type(h) == "table" and not e.enemy and not h.dead then
      local top = hero_top_level(hero)
      if (tonumber(hero.level) or 1) < top then
        hero.level = top
        -- 满级后经验不再有意义，直接落到末档阈值，避免游戏把它降回去
        local ok, gs = pcall(require, "game_settings")
        local th = ok and type(gs) == "table" and gs.hero_xp_thresholds
        if type(th) == "table" and type(th[top]) == "number" then hero.xp = th[top] end
        pcall(hero.fn_level_up, e)
        local ls = hero.level_stats
        if type(ls) == "table" and type(ls.hp_max) == "table" and ls.hp_max[top] then
          h.hp_max = ls.hp_max[top]
          h.hp = h.hp_max
        end
        KRT.hero_lv = top
        log("hero_max: level -> " .. tostring(top))
      end
    end
  end
end

local function apply_nocd(game)
  local g = game.game_gui
  if type(g) ~= "table" then return end
  for i = 1, 2 do
    local p = g["power_" .. i]
    if type(p) == "table" and type(p.cooldown_time) == "number" and p.cooldown_time > 0 then
      p.cooldown_time = 0
    end
  end
end

-- 援军总数：援军是 re_* 士兵模板（reinforcement 组件，duration=20s）。
--
-- 为什么不用「包装插入函数」：游戏生成援军不走 simulation.queue_insert_entity（实测包了没反应），
-- 而包装 edb.create_entity 还会被子弹/特效高频调用、位置也还没设置好。
-- 改成**看实体表**：每 tick 扫一遍 store.entities 里新出现的 re_* 实体，发现新面孔就补人。
-- 这条路与生成方式无关，不会漏，也不会重复（补出来的实体记在 _own 里，不会被再认成新面孔）。
local REINF_BASE = 2
local REINF_MAX = 10

local function reinf_extra()
  local total = tonumber(KRT.cfg and KRT.cfg.reinf_add) or 0
  if total > REINF_MAX then total = REINF_MAX end      -- 硬上限，防误填大数卡死
  local extra = total - REINF_BASE
  if extra <= 0 then return 0 end
  return extra
end

-- 补人：新建一个同模板援军，然后把「集结点 nav_rally.pos」和「移动目标 motion.dest」
-- 一起同步成源实体的值（模板里默认是 0,0，不同步就会朝左下角原点跑），再插队。
local function spawn_reinf_copies(game, store, src, extra, own)
  local sim = game and game.simulation
  local ok, edb = pcall(require, "entity_db")
  if not ok or type(edb) ~= "table" or type(sim) ~= "table" then return 0 end
  local tpl = src.template_name
  local x = type(src.pos) == "table" and src.pos.x or 0
  local y = type(src.pos) == "table" and src.pos.y or 0
  -- 集结点：援军的 nav_rally.pos 就是「呼叫点」。模板里默认是 (0,0)，
  -- 不跟着源实体设，副本就会朝左下角原点跑。
  local rx, ry = x, y
  local nr0 = src.nav_rally
  if type(nr0) == "table" and type(nr0.pos) == "table" then
    rx = tonumber(nr0.pos.x) or x
    ry = tonumber(nr0.pos.y) or y
  end
  -- create_entity 分配的 id 偶尔会和场上实体撞（撞了插队会被忽略），撞了就换一个新 id
  local maxid = 0
  for id in pairs(store.entities) do
    if type(id) == "number" and id > maxid then maxid = id end
  end
  local angle0 = math.random() * math.pi * 2
  local added = 0
  for i = 1, extra do
    local copy
    local okc, c = pcall(edb.create_entity, edb, tpl)
    if okc and type(c) == "table" then copy = c end
    if type(copy) == "table" and type(copy.pos) == "table" then
      local cid = copy.id
      if type(cid) ~= "number" or cid == src.id or store.entities[cid] ~= nil then
        maxid = maxid + 1
        copy.id = maxid
      end
      local a = angle0 + (i / extra) * math.pi * 2
      local px, py = rx + math.cos(a) * 18, ry + math.sin(a) * 18
      copy.pos.x, copy.pos.y = px, py
      -- 集结点/目的地都设成**它自己的位置**：设成源士兵的位置，8 个副本会自动走过去叠在一起
      local nr = copy.nav_rally
      if type(nr) == "table" then
        if type(nr.pos) == "table" then nr.pos.x, nr.pos.y = px, py end
        nr.new = false
      end
      local mo = copy.motion
      if type(mo) == "table" then
        if type(mo.dest) == "table" then mo.dest.x, mo.dest.y = px, py end
        mo.arrived = true
      end
      if pcall(sim.queue_insert_entity, sim, copy) then
        added = added + 1
        if own and copy.id then own[copy.id] = true end
      end
    end
  end
  return added
end

-- 援军持续时间：reinforcement.duration（默认 20 秒）。
-- 模板与场上实例都要写：模板管新生成的，实例管已经在场上的。
local function apply_reinf_time(store)
  local t = tonumber(KRT.cfg.reinf_time) or 0
  if t <= 0 then return end
  if t > 300 then t = 300 end
  if KRT._reinf_time_tpl ~= t then
    local ents = edb_entities()
    if ents then
      for name, tpl in pairs(ents) do
        if type(name) == "string" and name:find("^re_") and type(tpl) == "table"
            and type(tpl.reinforcement) == "table" then
          tpl.reinforcement.duration = t
        end
      end
    end
    KRT._reinf_time_tpl = t
  end
  for _, e in pairs(store.entities) do
    local r = e.reinforcement
    if type(r) == "table" and r.duration ~= t and type(e.template_name) == "string"
        and e.template_name:find("^re_") then
      r.duration = t
    end
  end
end

local function apply_reinf_watch(game, store)
  local extra = reinf_extra()
  if extra <= 0 then return end

  -- 换关卡时清空记录（id 会从头开始分配）
  local lv = store.level_name
  if KRT._reinf_level ~= lv then
    KRT._reinf_level = lv
    KRT._reinf_known = {}
    KRT._own = {}
  end
  local known = KRT._reinf_known or {}
  local own = KRT._own or {}
  KRT._reinf_known, KRT._own = known, own

  local fresh, ids = nil, {}
  for id, e in pairs(store.entities) do
    local tpl = e.template_name
    if type(tpl) == "string" and tpl:find("^re_") then
      ids[#ids + 1] = id
      if not own[id] and not known[id] then fresh = fresh or e end
    end
  end
  for i = 1, #ids do known[ids[i]] = true end   -- 先登记，避免波次窗口内被反复认成新面孔

  if not fresh then return end
  local now = os.clock()
  if now - (KRT._reinf_burst or -1) <= 0.6 then return end   -- 一次呼叫只补一波

  local added = spawn_reinf_copies(game, store, fresh, extra, own)
  if added > 0 then
    KRT._reinf_burst = now
    log("reinf: +" .. added .. " (" .. fresh.template_name .. ")")
  end
end

-- ================================================================ 04 全局
-- 自动下一波：模拟点击游戏内的“下一波”按钮（提前叫波，不跳过当前波）。
-- 按钮回调是 next_wave_button.on_click，禁用状态是 next_wave_button._disabled。
local function apply_auto_wave(game, store)
  if KRT.cfg.auto_wave ~= 1 then
    KRT.aw_called_wg = nil
    return
  end
  local cur = store.wave_group_number or 0
  local total = store.wave_group_total or 0
  if cur >= total then
    KRT.aw_called_wg = nil
    return
  end
  if KRT.aw_called_wg == cur then return end
  local gui = game and game.game_gui
  if type(gui) ~= "table" then return end
  local btn = gui.next_wave_button
  if type(btn) ~= "table" then
    KRT.aw_called_wg = cur
    return
  end
  if btn._disabled then return end
  local fn = btn.on_click
  if type(fn) ~= "function" then
    KRT.aw_called_wg = cur
    return
  end
  KRT.aw_called_wg = cur
  pcall(fn, btn)
end

-- 速度：不放大 dt，而是每帧补跑若干次固定步长的模拟步。
local function hook_tick(sim)
  if KRT._tick_hooked == sim then return end
  local orig = sim.do_tick
  if type(orig) ~= "function" then return end
  KRT._tick_hooked = sim
  sim.do_tick = function(self, ...)
    if not KRT._in_extra then
      KRT._game_stepped = true
    end
    return orig(self, ...)
  end
end

local function apply_speed()
  local sp = KRT.speed or 1
  if sp <= 1.001 then
    KRT.tick_acc = 0
    return
  end
  local game = active_game()
  if not game then return end
  local st = game.store
  if type(st) == "table" and st.paused then
    KRT.tick_acc = 0
    return
  end
  local sim = game.simulation
  if type(sim) ~= "table" or type(sim.do_tick) ~= "function" then return end
  hook_tick(sim)
  if not KRT._game_stepped then
    KRT.tick_acc = 0
    return
  end
  KRT._game_stepped = false
  KRT.tick_acc = (KRT.tick_acc or 0) + (sp - 1)
  local n = math.floor(KRT.tick_acc)
  if n <= 0 then return end
  KRT.tick_acc = KRT.tick_acc - n
  if n > 24 then n = 24 end
  KRT._in_extra = true
  for _ = 1, n do
    if not pcall(sim.do_tick, sim) then break end
  end
  KRT._in_extra = false
end
KRT.apply_speed = apply_speed

-- ---------------------------------------------------------------- main apply
local function apply()
  local cfg = KRT.cfg
  local now = os.clock()
  local dt = now - (KRT.last_apply or now)
  if dt < 0 or dt > 1 then dt = 0.1 end
  KRT.last_apply = now

  local game = active_game()
  if not game then
    KRT.level_key = nil
    KRT.counted = {}
    return
  end
  local store = game.store

  apply_gold(store, dt)
  if cfg.lives_lock == 1 then
    store.lives = math.floor(tonumber(cfg.lives_value) or 0)
  end
  if cfg.heal_friend == 1 or cfg.invincible == 1 then
    heal_friendlies(store)
  end

  local key = tostring(store.level_name) .. "#" .. tostring(store.level_idx)
  if KRT.level_key ~= key then
    KRT.level_key = key
    KRT.counted = {}
  end

  kill_bonus(store)
  pcall(apply_towers, store)
  if cfg.nocd == 1 then apply_nocd(game) end
  apply_hero_max(store)
  if (tonumber(cfg.reinf_add) or 0) > 0 then apply_reinf_watch(game, store) end
  if (tonumber(cfg.reinf_time) or 0) > 0 then apply_reinf_time(store) end
  apply_auto_wave(game, store)
end

-- ---------------------------------------------------------------- 调试通道
-- 外部写入 cmd.txt 即可执行一段 Lua（只用于排障，正常使用时不存在该文件）
local function poll_cmd()
  local src = readfile(BASE .. "cmd.txt")
  if not src then return end
  if src == KRT.last_cmd then return end
  KRT.last_cmd = src
  local f, err = loadstring(src, "=cmd")
  if not f then
    writefile(BASE .. "out.txt", "COMPILE_ERROR: " .. tostring(err))
    return
  end
  local ok, res = pcall(f)
  if not ok then
    writefile(BASE .. "out.txt", "RUNTIME_ERROR: " .. tostring(res))
  else
    writefile(BASE .. "out.txt", "OK: " .. tostring(res))
  end
end

-- ---------------------------------------------------------------- status
local function status()
  local lines = {
    "v=" .. KRT.version,
    "fps=" .. tostring(love and love.timer and math.floor(1 / (love.timer.getDelta() + 1e-6) + 0.5) or 0),
    "speed=" .. string.format("%.2f", KRT.speed or 1),
    "towers=" .. tostring(KRT.tw_count or 0),
  }
  local game = active_game()
  if game then
    local s = game.store
    lines[#lines + 1] = "state=ingame"
    lines[#lines + 1] = "gold=" .. tostring(s.player_gold)
    lines[#lines + 1] = "lives=" .. tostring(s.lives)
    lines[#lines + 1] = "wave=" .. tostring(s.wave_group_number) .. "/" .. tostring(s.wave_group_total)
    lines[#lines + 1] = "level=" .. tostring(s.level_name)
  else
    lines[#lines + 1] = "state=menu"
  end
  writefile(STATUS, table.concat(lines, "\n"))
end

-- ---------------------------------------------------------------- hooks
local wrapped = setmetatable({}, { __mode = "k" })
KRT.wrapped = wrapped
KRT.last_tick = 0

local function tick_work()
  if GEN ~= _G.__KRT_GEN then return end
  local now = os.clock()
  if now - (KRT.last_tick or 0) < 0.1 then return end
  KRT.last_tick = now
  pcall(load_state)
  pcall(apply)
  pcall(poll_cmd)

  if now - (KRT.last_status or 0) > 0.4 then
    KRT.last_status = now
    pcall(status)
  end
  if now - (KRT.last_attach or 0) > 2 then
    KRT.last_attach = now
    if KRT.attach then pcall(KRT.attach) end
  end
end

local function hook_fn(tbl, key)
  if type(tbl) ~= "table" then return end
  local f = tbl[key]
  if type(f) ~= "function" then return end
  local set = wrapped[tbl]
  if not set then set = {} wrapped[tbl] = set end
  if set[key] == f then return end
  local wrapper = function(...)
    local a, b, c, d = f(...)
    pcall(tick_work)
    return a, b, c, d
  end
  set[key] = wrapper
  tbl[key] = wrapper
end

local function check_update()
  if GEN ~= _G.__KRT_GEN then return end
  if type(love) ~= "table" then return end
  local f = love.update
  if type(f) ~= "function" then return end
  local set = wrapped[love]
  if not set then set = {} wrapped[love] = set end
  if set.update_patched == f then return end
  local wrapper = function(dt)
    local a, b, c = f(dt)
    pcall(tick_work)
    pcall(apply_speed)
    return a, b, c
  end
  set.update_patched = wrapper
  love.update = wrapper
end

local function attach()
  if GEN ~= _G.__KRT_GEN then return end
  if type(love) ~= "table" then return end
  if love.graphics then hook_fn(love.graphics, "present") end
  if love.timer then hook_fn(love.timer, "step") end
  hook_fn(love, "draw")
  check_update()
end
KRT.attach = attach

pcall(load_state)
-- 排障入口：cmd.txt 可调用 KRT.scan_towers() / KRT.apply_towers(store)
-- 清掉上一代留在旧壳里的处理函数（旧壳仍在 sim / edb 上，不清就会继续跑旧逻辑）
_G.__KRT_REINF_HANDLER = nil
_G.__KRT_CREATE_HANDLER = nil

KRT.scan_towers = scan_towers
KRT.apply_towers = apply_towers
KRT.apply_reinf_time = apply_reinf_time
-- 载入时先记住已有的 cmd.txt，避免重载时把旧命令再执行一遍（会互相触发死循环）
KRT.last_cmd = readfile(BASE .. "cmd.txt")

pcall(attach)
if not readfile(TOWERS) then pcall(scan_towers) end
pcall(status)
log("payload v" .. KRT.version .. " loaded")
