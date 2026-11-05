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
  version = "2.2",
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

local TW_FIELD = { spd = true, dmg = true, rng = true, sold = true }

local function tower_mults()
  local cfg = KRT.cfg
  if KRT._mult_cfg == cfg then return KRT._mult end
  local out = {}
  for k, v in pairs(cfg) do
    local name, field = tostring(k):match("^tw_(.+)_([a-z]+)$")
    if name and TW_FIELD[field] then
      local t = out[name]
      if not t then t = {} out[name] = t end
      t[field] = tonumber(v) or 0
    end
  end
  KRT._mult_cfg, KRT._mult = cfg, out
  return out
end

-- 兵营士兵数上限（界面同值）
local SOLD_MAX = 20

-- 士兵数基准值缓存：模板 barrack 组件 → 游戏原始 max_soldiers。
-- 跨世代保留（热重载时模板已被上一代改过，重建缓存会把改后的值当成基准）。
local sold_base = _G.__KRT_SOLD_BASE
if not sold_base then
  sold_base = setmetatable({}, { __mode = "k" })
  _G.__KRT_SOLD_BASE = sold_base
end
KRT.sold_base = sold_base

-- 目标士兵数；sold <= 0 表示还原。返回 (目标值, 原始值)
local function barrack_soldiers(tb, sold)
  if type(tb) ~= "table" or type(tb.max_soldiers) ~= "number" then return nil end
  local base = sold_base[tb]
  if not base then
    base = tb.max_soldiers
    sold_base[tb] = base
  end
  if sold <= 0 then return base, base end
  local n = math.floor(sold + 0.5)
  if n < 1 then n = 1 elseif n > SOLD_MAX then n = SOLD_MAX end
  return n, base
end

-- 升级时新塔的 max_soldiers 取自**下一级模板**：只改实例的话，
-- 升级会按模板的 3 人处理，多出来的士兵被就地解散、再让新塔从门里重新出人。
-- 所以同系的每张兵营模板都要写（新建/升级都走模板），实例值另外每帧覆盖。
local function apply_barrack_templates(sold)
  local ents = edb_entities()
  if not ents then return end
  if KRT._sold_ents == ents and KRT._sold_val == sold then return end
  KRT._sold_ents, KRT._sold_val = ents, sold
  for name, tpl in pairs(ents) do
    if type(name) == "string" and name:find("^tower_") and type(tpl) == "table" then
      local tw = tpl.tower
      local tb = tpl.barrack
      if type(tb) == "table" and type(tw) == "table" and FAMILY_OF[tw.type] == "barrack" then
        local n = barrack_soldiers(tb, sold)
        if n and tb.max_soldiers ~= n then tb.max_soldiers = n end
      end
    end
  end
end

-- 用模板原型做基准，每帧覆写；关闭时自动还原，不会累积放大
-- sold：兵营士兵数（兵营 update 每 tick 按 barrack.max_soldiers 补人，改这里就会补到指定人数）
local function apply_tower(e, tpl, spd, dmg, rng, sold, ents)
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
  if eb and tb then
    if type(eb.rally_range) == "number" and type(tb.rally_range) == "number" then
      eb.rally_range = (rng > 0) and tb.rally_range * rng or tb.rally_range
    end
    local n = barrack_soldiers(tb, sold)
    if n then
      if tb.max_soldiers ~= n then tb.max_soldiers = n end        -- 模板：升级/新建时继承
      if type(eb.max_soldiers) == "number" and eb.max_soldiers ~= n then
        eb.max_soldiers = n                                        -- 实例：现有塔立刻生效
      end
    end
  end
end

local function apply_towers(store)
  local ents = edb_entities()
  local mults = tower_mults()
  local bm = mults.barrack
  apply_barrack_templates(bm and bm.sold or 0)
  for _, e in pairs(store.entities) do
    local tw = e.tower
    local name = e.template_name
    if tw and type(name) == "string" and name:find("^tower_") then
      local m = FAMILY_OF[tw.type] and mults[FAMILY_OF[tw.type]]
      local spd = m and m.spd or 0
      local dmg = m and m.dmg or 0
      local rng = m and m.rng or 0
      local sold = m and m.sold or 0
      if spd > 0 or dmg > 0 or rng > 0 or sold > 0 then
        KRT.tw_touched[e] = true
        apply_tower(e, ents and ents[name], spd, dmg, rng, sold, ents)
      elseif KRT.tw_touched[e] then
        KRT.tw_touched[e] = nil
        apply_tower(e, ents and ents[name], 0, 0, 0, 0, ents)
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
      local soldiers = 0
      local tb = tpl.barrack
      if type(tb) == "table" and type(tb.max_soldiers) == "number" then
        soldiers = sold_base[tb] or tb.max_soldiers      -- 回报游戏原始值，不是我们改后的
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
        soldiers = soldiers,
        order = (FAMILY[tpl.tower.type] or 9) * 100 + (tpl.tower.level or 0),
      }
    end
  end
  table.sort(list, function(a, b)
    if a.order ~= b.order then return a.order < b.order end
    return a.name < b.name
  end)

  local lines = { "# tpl|zh|type|level|price|cd|dmg_min|dmg_max|range|bullet|family|soldiers" }
  for _, t in ipairs(list) do
    lines[#lines + 1] = table.concat({
      t.name, t.zh, t.type, tostring(t.level), tostring(t.price),
      string.format("%.3f", t.cd), string.format("%.2f", t.dmin),
      string.format("%.2f", t.dmax), string.format("%.2f", t.range), t.bullet,
      FAMILY_OF[t.type] or "", tostring(t.soldiers),
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

-- 技能冷却：按钮的 cooldown_time 只在创建时从模板 cooldown 赋值一次，
-- 直接写 0 之后游戏自己不会还原，所以关闭开关要手动写回原值（否则本局一直无冷却）。
local POWER_TPL = { "user_power_1", "re_current_1" }   -- power_1 火雨 / power_2 援军
local cd_base = setmetatable({}, { __mode = "k" })
KRT.cd_base = cd_base

local function power_template_cd(i)
  local ents = edb_entities()
  local tpl = ents and ents[POWER_TPL[i]]
  if type(tpl) == "table" and type(tpl.cooldown) == "number" and tpl.cooldown > 0 then
    return tpl.cooldown
  end
end

local function apply_nocd(game)
  local g = game.game_gui
  if type(g) ~= "table" then return end
  local on = KRT.cfg.nocd == 1
  for i = 1, #POWER_TPL do
    local p = g["power_" .. i]
    if type(p) == "table" and type(p.cooldown_time) == "number" then
      local base = cd_base[p]
      if not base then
        -- 基准优先用模板 cooldown（按钮创建时就是拿它赋值的），查不到再记首次见到的值
        base = power_template_cd(i) or ((p.cooldown_time > 0) and p.cooldown_time) or 2
        cd_base[p] = base
      end
      local want = on and 0 or base
      if p.cooldown_time ~= want then p.cooldown_time = want end
    end
  end
end

-- 援军总数：游戏点一次援军按钮，Power2Button.fire 里同步建 2 个 re_* 士兵。
--
-- 走两条路：
--   1) 主路：包住 Power2Button.fire，点一次补一次 —— 位置就是点击点，不依赖扫描/计时，
--      反复连点也不会漏（旧实现用 0.6s 时间窗去重，连点会被整批吞掉，表现就是只出 2 个）；
--   2) 兜底：拿不到 Power2Button（版本差异）时扫实体表，已见记录按**实体对象**存。
--      旧实现按 id 存，id 复用后新兵会被当成老面孔，同样表现为只出 2 个。
local REINF_MAX = 10

local function reinf_total()
  local t = tonumber(KRT.cfg and KRT.cfg.reinf_add) or 0
  if t <= 0 then return 0 end
  if t > REINF_MAX then t = REINF_MAX end      -- 硬上限，防误填大数卡死
  return math.floor(t)
end

-- 补人：新建同模板援军，位置/集结点/移动目标都设成它自己的生成点
-- （模板里是 0,0，不同步就会朝左下角原点跑）。cx/cy 为空时用源士兵的位置。
local function spawn_reinf_copies(game, store, src, count, own, cx, cy)
  local sim = game and game.simulation
  local ok, edb = pcall(require, "entity_db")
  if not ok or type(edb) ~= "table" or type(sim) ~= "table" or count <= 0 then return 0 end
  local tpl = src and src.template_name
  if type(tpl) ~= "string" then return 0 end
  local x, y = cx, cy
  if type(x) ~= "number" or type(y) ~= "number" then
    x = type(src.pos) == "table" and src.pos.x or 0
    y = type(src.pos) == "table" and src.pos.y or 0
    local nr0 = src.nav_rally
    if type(nr0) == "table" and type(nr0.pos) == "table" then
      x = tonumber(nr0.pos.x) or x
      y = tonumber(nr0.pos.y) or y
    end
  end
  local angle0 = math.random() * math.pi * 2
  local added = 0
  for i = 1, count do
    local okc, c = pcall(edb.create_entity, edb, tpl)
    local copy = okc and c or nil
    if type(copy) == "table" and type(copy.pos) == "table" then
      local cid = copy.id
      if type(cid) ~= "number" or store.entities[cid] ~= nil or (src and cid == src.id) then
        -- id 撞了就换一个空的，同时推进 edb 计数器，避免后续实体再撞
        local nid = tonumber(edb.last_id) or 1
        repeat nid = nid + 1 until store.entities[nid] == nil
        edb.last_id = nid + 1
        copy.id = nid
      end
      local a = angle0 + (i / count) * math.pi * 2
      local px, py = x + math.cos(a) * 20, y + math.sin(a) * 20
      copy.pos.x, copy.pos.y = px, py
      -- 集结点设成点击点、目的地设成自己脚下：副本各自站位，不会叠在一起
      local nr = copy.nav_rally
      if type(nr) == "table" then
        if type(nr.center) == "table" then nr.center.x, nr.center.y = x, y end
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
        if own then own[copy] = true end
      end
    end
  end
  return added
end

local function reinf_seen()
  if not KRT._reinf_known then KRT._reinf_known = setmetatable({}, { __mode = "k" }) end
  if not KRT._own then KRT._own = setmetatable({}, { __mode = "k" }) end
  return KRT._reinf_known, KRT._own
end

-- 找这一批新兵（id >= since 的 re_*），返回首个样本与数量
local function reinf_new_batch(store, since)
  local src, n = nil, 0
  for _, e in pairs(store.entities) do
    local t = e.template_name
    if type(t) == "string" and t:find("^re_") and type(e.id) == "number" and e.id >= since then
      n = n + 1
      src = src or e
    end
  end
  return src, n
end

-- 点击后的补人：本批已经补齐 total 个，多的由这里加，少的直接跳过
local function reinf_after_click(wx, wy, before_id)
  local game = active_game()
  local store = game and game.store
  if type(store) ~= "table" then return end
  local known, own = reinf_seen()
  local src, n = reinf_new_batch(store, before_id)
  for _, e in pairs(store.entities) do
    local t = e.template_name
    if type(t) == "string" and t:find("^re_") and type(e.id) == "number" and e.id >= before_id then
      known[e] = true                     -- 已处理，兜底扫描别重复补
    end
  end
  local total = reinf_total()
  if not src or total <= 0 then return end
  local add = total - n
  if add <= 0 then return end
  local added = spawn_reinf_copies(game, store, src, add, own, wx, wy)
  if added > 0 then log("reinf: +" .. added .. " (" .. src.template_name .. ")") end
end

-- 包装 Power2Button.fire（援军按钮）。热重载时先拆上一代的包装再装新的，避免多层套娃。
local function install_reinf_hook()
  local cls = _G.Power2Button
  if type(cls) ~= "table" or type(cls.fire) ~= "function" then return false end
  local old = _G.__KRT_REINF_HOOK
  if type(old) == "table" and old.wrapper and cls.fire == old.wrapper then
    if KRT.reinf_hooked then return true end
    cls.fire = old.orig
  end
  local orig = cls.fire
  local wrapper = function(self, wx, wy)
    local before
    local ok, edb = pcall(require, "entity_db")
    if ok and type(edb) == "table" then before = tonumber(edb.last_id) end
    local a, b, c = orig(self, wx, wy)
    if GEN == _G.__KRT_GEN and before then pcall(reinf_after_click, wx, wy, before) end
    return a, b, c
  end
  cls.fire = wrapper
  _G.__KRT_REINF_HOOK = { orig = orig, wrapper = wrapper }
  KRT.reinf_hooked = true
  log("reinf hook installed")
  return true
end
KRT.install_reinf_hook = install_reinf_hook

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

-- 钩子是否还在（游戏重建类/换脚本会把 cls.fire 换掉）
local function reinf_hook_alive()
  local cls = _G.Power2Button
  local h = _G.__KRT_REINF_HOOK
  return KRT.reinf_hooked and type(cls) == "table" and type(h) == "table"
         and type(h.wrapper) == "function" and cls.fire == h.wrapper
end

-- 兜底：没有 Power2Button 可包时，扫实体表补人（按实体对象去重，不再按 id）
local function apply_reinf_watch(game, store)
  if KRT.reinf_hooked then
    if reinf_hook_alive() then return end
    pcall(install_reinf_hook)          -- 类被重建：立刻重装
    if reinf_hook_alive() then return end
  end
  -- 换关卡时清空记录（id 会从头开始分配）
  local lv = tostring(store.level_name) .. "#" .. tostring(store.level_idx)
  if KRT._reinf_level ~= lv then
    KRT._reinf_level = lv
    KRT._reinf_known = setmetatable({}, { __mode = "k" })
    KRT._own = setmetatable({}, { __mode = "k" })
  end
  local known, own = reinf_seen()

  local fresh, n, batch = nil, 0, {}
  for _, e in pairs(store.entities) do
    local t = e.template_name
    if type(t) == "string" and t:find("^re_") and not own[e] and not known[e] then
      n = n + 1
      fresh = fresh or e
      batch[#batch + 1] = e
    end
  end
  -- 不论开关都先登记：中途打开开关时，场上已有的援军不会被当成「刚召的」而白补一波
  for i = 1, #batch do known[batch[i]] = true end

  local total = reinf_total()
  if not fresh or total <= 0 then return end
  local add = total - n
  if add <= 0 then return end

  local added = spawn_reinf_copies(game, store, fresh, add, own)
  if added > 0 then log("reinf(fallback): +" .. added .. " (" .. fresh.template_name .. ")") end
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
  apply_nocd(game)          -- 关闭时也要跑：把 cooldown_time 写回模板值
  apply_hero_max(store)
  apply_reinf_watch(game, store)
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
  pcall(install_reinf_hook)     -- 每 2s 自愈一次：类被重建也能重新包上
  if love.graphics then hook_fn(love.graphics, "present") end
  if love.timer then hook_fn(love.timer, "step") end
  hook_fn(love, "draw")
  check_update()
end
KRT.attach = attach

pcall(load_state)
-- 排障入口：cmd.txt 可调用 KRT.scan_towers() / KRT.apply_towers(store)
-- 旧壳的援军钩子由 install_reinf_hook 拆（保留 __KRT_REINF_HOOK，才能拿回原始 fire）

KRT.scan_towers = scan_towers
KRT.apply_towers = apply_towers
KRT.apply = apply
KRT.apply_reinf_time = apply_reinf_time
-- 载入时先记住已有的 cmd.txt，避免重载时把旧命令再执行一遍（会互相触发死循环）
KRT.last_cmd = readfile(BASE .. "cmd.txt")

pcall(attach)
if not readfile(TOWERS) then pcall(scan_towers) end
pcall(status)
log("payload v" .. KRT.version .. " loaded")
