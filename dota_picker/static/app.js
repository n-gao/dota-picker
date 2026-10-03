"use strict";

const ICONS = {
  sword: '<path d="M14.5 3.5H20.5V9.5L9.5 20.5 3.5 14.5Z M6 12l6 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  target: '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
  shield: '<path d="M12 3l8 3v6c0 4.8-3.4 8-8 9-4.6-1-8-4.2-8-9V6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  bolt: '<path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
  warn: '<path d="M12 3 22 20H2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4M12 17v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
};

const POSITIONS = [
  { id: 1, label: "Carry", icon: "sword" },
  { id: 2, label: "Mid", icon: "target" },
  { id: 3, label: "Offlane", icon: "shield" },
  { id: 4, label: "Soft sup", icon: "bolt" },
  { id: 5, label: "Hard sup", icon: "eye" },
];
// Lane (Dotabuff lane stats) for each position, and which enemy positions you face in lane.
const POS_LANE = { 1: "safe", 2: "mid", 3: "off", 4: "off", 5: "safe" };
const LANE_OPPONENTS = { 1: [3, 4], 2: [2], 3: [1, 5], 4: [1, 5], 5: [3, 4] };

// Sample-size shrinkage: a stat over n games is trusted n / (n + K). Matchups and
// position win rates are pulled towards "even" (rank-specific win rates towards all ranks).
const K_MATCHUP = 3000;
const K_POSITION = 3000;
const DANGER_ADV = -3;   // flag suggestions an enemy counters at least this hard
// Off-role picks (e.g. carry Leshrac) are played mostly by specialists, which inflates their
// win rate. Trust the position win rate fully only once the role is ROLE_FULL_SHARE% of the hero's games.
const ROLE_FULL_SHARE = 40;

// Common community nicknames that prefix/initials matching won't catch.
const ALIASES = {
  am: "anti-mage", cm: "crystal-maiden", wr: "windranger", qop: "queen-of-pain", sf: "shadow-fiend",
  pa: "phantom-assassin", ta: "templar-assassin", ns: "night-stalker", ls: "lifestealer",
  lc: "legion-commander", dk: "dragon-knight", wk: "wraith-king", bh: "bounty-hunter", sk: "sand-king",
  ck: "chaos-knight", kotl: "keeper-of-the-light", potm: "mirana", wd: "witch-doctor",
  aa: "ancient-apparition", bb: "bristleback", bs: "bloodseeker", np: "natures-prophet",
  furion: "natures-prophet", ld: "lone-druid", mk: "monkey-king", sb: "spirit-breaker",
  sd: "shadow-demon", ss: "shadow-shaman", tb: "terrorblade", dp: "death-prophet", et: "elder-titan",
  wisp: "io", od: "outworld-destroyer", cent: "centaur-warrunner", clock: "clockwerk", dusa: "medusa",
  veno: "venomancer", venge: "vengeful-spirit", vs: "vengeful-spirit", lesh: "leshrac",
  jugg: "juggernaut", morph: "morphling", necro: "necrophos", omni: "omniknight", pango: "pangolier",
  timber: "timbersaw", ench: "enchantress", bat: "batrider", brew: "brewmaster", alch: "alchemist",
  shaker: "earthshaker", pl: "phantom-lancer", sven: "sven", void: "faceless-void",
  fv: "faceless-void", tiny: "tiny", sniper: "sniper", ember: "ember-spirit", storm: "storm-spirit",
  earth: "earth-spirit", ogre: "ogre-magi", troll: "troll-warlord", ursa: "ursa", invo: "invoker",
  kez: "kez", pb: "primal-beast", grim: "grimstroke", gyro: "gyrocopter", brood: "broodmother",
  doom: "doom", dazz: "dazzle", hood: "hoodwink", rm: "ringmaster", wl: "warlock", ww: "winter-wyvern",
  wyvern: "winter-wyvern", tide: "tidehunter", tech: "techies", sky: "skywrath-mage",
  spec: "spectre", abba: "abaddon", ab: "abaddon", arc: "arc-warden", aw: "arc-warden",
};

const state = {
  data: null,
  heroes: [],          // hero records sorted by name
  position: 1,
  rank: "auto",        // "auto", "all" or a rank tier
  enemies: [],         // slugs
  excluded: [],        // slugs
  minShare: 10,
  metaWeight: 1,
  laneWeight: 1,
  comfortWeight: 1,
  minGames: 5,
  playerId: "",
  player: null,
  playerPeriod: "all", // "all" (all time) or "recent" (last 12 months)
  poolMode: "all",
  drawer: null,        // slug of the hero shown in the detail drawer
  rows: new Map(),     // slug -> scored row from the last render
};
const buildCache = new Map(); // "slug:pos" -> build JSON (or a pending Promise)

const $ = (id) => document.getElementById(id);
const img = (slug) => `/img/${slug}.jpg`;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const norm = (s) => s.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
const fmt = (n, d = 1) => (n > 0 ? "+" : n < 0 ? "−" : "±") + Math.abs(n).toFixed(d);
const tone = (n, eps = 0.25) => (n > eps ? "up" : n < -eps ? "down" : "flat");
const kfmt = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n));
const cap = (s) => s[0].toUpperCase() + s.slice(1);
const hero = (slug) => state.data.heroes[slug];
const posName = (p) => POSITIONS.find((x) => x.id === p).label;

// Diverging tint for matchup chips: blue = good for you, red = bad, gray = even.
function chipColor(adv) {
  const t = Math.min(Math.abs(adv) / 5, 1);
  const rgb = adv >= 0 ? "76,155,232" : "229,87,75";
  return `rgba(${rgb},${(0.12 + 0.5 * t).toFixed(2)})`;
}

// ---------- persistence ----------
const SAVED = ["position", "rank", "enemies", "excluded", "minShare", "metaWeight", "laneWeight",
  "comfortWeight", "minGames", "playerId", "playerPeriod", "poolMode"];
function save() {
  try {
    localStorage.setItem("dota-picker", JSON.stringify(Object.fromEntries(SAVED.map((k) => [k, state[k]]))));
  } catch {}
}
function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem("dota-picker") || "{}");
    for (const k of SAVED) if (k in saved) state[k] = saved[k];
  } catch {}
}

// ---------- search ----------
function searchHeroes(query, taken) {
  const q = norm(query);
  if (!q) return [];
  const results = [];
  for (const h of state.heroes) {
    if (taken.has(h.slug)) continue;
    const name = norm(h.name);
    const words = name.split(" ");
    const initials = words.map((w) => w[0]).join("");
    let rank = 0;
    if (ALIASES[q] === h.slug) rank = 100;
    else if (name === q) rank = 95;
    else if (name.startsWith(q)) rank = 80;
    else if (initials === q) rank = 75;
    else if (words.some((w) => w.startsWith(q))) rank = 60;
    else if (name.replace(/ /g, "").includes(q.replace(/ /g, ""))) rank = 40;
    else if (q.length >= 2 && initials.startsWith(q)) rank = 30;
    if (rank) results.push({ h, rank: rank + h.pickRate / 100 });
  }
  return results.sort((a, b) => b.rank - a.rank).slice(0, 8).map((r) => r.h);
}

function attachSearch(input, list, onPick, getTaken, onEmptyKey) {
  let items = [];
  let active = 0;

  const render = () => {
    items = searchHeroes(input.value, getTaken());
    active = 0;
    list.hidden = items.length === 0;
    list.innerHTML = items.map((h, i) =>
      `<li data-i="${i}" class="${i === active ? "active" : ""}"><img src="${img(h.slug)}" alt="">${esc(h.name)}</li>`
    ).join("");
  };
  const highlight = () => list.querySelectorAll("li").forEach((li, i) => li.classList.toggle("active", i === active));
  const pick = (h) => {
    if (!h) return;
    onPick(h.slug);
    input.value = "";
    render();
  };

  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && items.length) { active = (active + 1) % items.length; highlight(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && items.length) { active = (active - 1 + items.length) % items.length; highlight(); e.preventDefault(); }
    else if (e.key === "Enter" || (e.key === "Tab" && items.length)) { pick(items[active]); e.preventDefault(); }
    else if (e.key === "Escape") { input.value = ""; render(); }
    else if (!input.value && onEmptyKey && onEmptyKey(e)) e.preventDefault();
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li");
    if (li) { pick(items[+li.dataset.i]); e.preventDefault(); }
  });
  input.addEventListener("blur", () => { list.hidden = true; });
  input.addEventListener("focus", render);
}

// ---------- model ----------
function effectiveRank() {
  if (state.rank !== "auto") return state.rank;
  return (state.player && state.player.rank) || "all";
}

function positionShares(h) {
  const all = h.positions.all || {};
  const counts = [1, 2, 3, 4, 5].map((p) => (all[p] ? all[p][0] : 0));
  const total = counts.reduce((a, b) => a + b, 0) || 1;
  return counts.map((c) => c / total);
}

/** Position stats for the selected rank, with the win rate shrunk by sample size. */
function positionStats(h, pos, rank) {
  const all = (h.positions.all || {})[pos];
  if (!all) return null;
  const allAdj = 50 + (all[1] - 50) * (all[0] / (all[0] + K_POSITION));
  if (rank === "all") return { matches: all[0], winRate: all[1], adjusted: allAdj, rank };
  const r = (h.positions[rank] || {})[pos];
  if (!r) return { matches: 0, winRate: null, adjusted: allAdj, rank };
  return { matches: r[0], winRate: r[1], adjusted: (r[0] * r[1] + K_POSITION * allAdj) / (r[0] + K_POSITION), rank };
}

/**
 * P(enemy i plays position p), considering the whole draft: every way of assigning the
 * enemies to distinct positions is weighted by the product of their position shares.
 */
function inferRoles(enemies) {
  const shares = enemies.map((s) => positionShares(hero(s)).map((x) => x + 0.002));
  const probs = enemies.map(() => [0, 0, 0, 0, 0]);
  let total = 0;
  const assigned = [];
  const used = [false, false, false, false, false];
  (function walk(i, w) {
    if (i === enemies.length) {
      total += w;
      assigned.forEach((p, j) => { probs[j][p] += w; });
      return;
    }
    for (let p = 0; p < 5; p++) {
      if (used[p]) continue;
      used[p] = true; assigned[i] = p;
      walk(i + 1, w * shares[i][p]);
      used[p] = false;
    }
  })(0, 1);
  return probs.map((row) => row.map((x) => x / total));
}

function advantage(a, b) {
  // a's advantage over b, in %. Prefer a's own counters page, fall back to b's.
  const m = state.data.matchups;
  let raw = null;
  if (m[a] && m[a][b]) raw = { raw: m[a][b][0], wr: m[a][b][1], n: m[a][b][2] };
  else if (m[b] && m[b][a]) raw = { raw: -m[b][a][0], wr: 100 - m[b][a][1], n: m[b][a][2] };
  if (!raw) return { raw: 0, adv: 0, wr: null, n: 0 };
  return { ...raw, adv: raw.raw * (raw.n / (raw.n + K_MATCHUP)) };
}

function enemyContext() {
  const roles = inferRoles(state.enemies);
  const opp = LANE_OPPONENTS[state.position];
  return state.enemies.map((slug, i) => {
    const probs = roles[i];
    const best = probs.indexOf(Math.max(...probs));
    return { slug, probs, role: best + 1, roleP: probs[best], laneP: opp.reduce((s, p) => s + probs[p - 1], 0) };
  });
}

function playerPool() {
  return state.player ? state.player.heroes[state.playerPeriod] || {} : {};
}

function comfort(slug) {
  const p = playerPool()[slug];
  if (!p) return { games: 0, wins: 0, bonus: 0 };
  return { games: p.games, wins: p.wins, bonus: 4 * Math.sqrt(Math.min(p.games, 100) / 100) };
}

/** Score every hero that fits the position (ignores the "My heroes" filter). */
function scoreAll(enemies) {
  const pos = String(state.position);
  const rank = effectiveRank();
  const blocked = new Set([...state.enemies, ...state.excluded]);
  const rows = [];
  for (const h of state.heroes) {
    if (blocked.has(h.slug)) continue;
    const ps = positionStats(h, pos, rank);
    if (!ps) continue;
    const share = positionShares(h)[state.position - 1] * 100;
    if (share < state.minShare) continue;
    const you = comfort(h.slug);

    const vs = enemies.map((e) => ({ ...e, ...advantage(h.slug, e.slug) }));
    const parts = {
      counters: vs.reduce((s, v) => s + v.adv, 0),
      lane: state.laneWeight * vs.reduce((s, v) => s + v.adv * v.laneP, 0),
      meta: state.metaWeight * Math.min(1, share / ROLE_FULL_SHARE) * (ps.adjusted - 50),
      comfort: state.comfortWeight * you.bonus,
    };
    const score = parts.counters + parts.lane + parts.meta + parts.comfort;
    const worst = vs.reduce((w, v) => (!w || v.adv < w.adv ? v : w), null);
    rows.push({
      h, ps, share, vs, you, parts, score,
      laneRaw: vs.reduce((s, v) => s + v.adv * v.laneP, 0),
      danger: worst && worst.adv <= DANGER_ADV ? worst : null,
      trend: (h.trend || {})[pos],
      laneStats: h.lanes[POS_LANE[state.position]],
    });
  }
  return rows;
}

// ---------- rendering helpers ----------
function renderPositions() {
  $("positions").innerHTML = POSITIONS.map((p) =>
    `<button role="radio" aria-checked="${p.id === state.position}" data-pos="${p.id}" title="Position ${p.id} — press ${p.id}">
       <span class="num">${p.id}</span><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[p.icon]}</svg><span class="lbl">${p.label}</span></button>`
  ).join("");
}

function renderRankSelect() {
  const auto = state.player && state.player.rank ? `Auto · ${cap(state.player.rank)}` : "Auto · all ranks";
  const opts = [["auto", auto], ["all", "All ranks"], ...state.data.ranks.map((r) => [r, cap(r)])];
  $("rank").innerHTML = opts.map(([v, l]) => `<option value="${v}" ${v === state.rank ? "selected" : ""}>${l}</option>`).join("");
}

function renderSlots(el, slugs, count, onRemove, enemies) {
  const cells = [];
  const n = count ?? slugs.length;
  for (let i = 0; i < n; i++) {
    const s = slugs[i];
    if (!s) { cells.push(`<div class="slot">${i + 1}</div>`); continue; }
    const e = enemies && enemies[i];
    const role = e
      ? `<span class="role ${e.laneP >= 0.5 ? "lane" : ""}" title="Likely position ${e.role} (${Math.round(e.roleP * 100)}%) · ${Math.round(e.laneP * 100)}% chance in your lane">P${e.role}</span>`
      : "";
    cells.push(`<button class="slot filled" data-slug="${s}" title="Remove ${esc(hero(s).name)}">
       <img src="${img(s)}" alt="">${role}<span class="name">${esc(hero(s).name)}</span></button>`);
  }
  el.innerHTML = cells.join("");
  el.onclick = (ev) => {
    const b = ev.target.closest(".slot.filled");
    if (b) onRemove(b.dataset.slug);
  };
}

function matchupTip(r, v) {
  return `${r.h.name} vs ${hero(v.slug).name}: ${fmt(v.adv, 2)}% advantage` +
    (v.wr != null ? ` (raw ${fmt(v.raw, 2)}%, ${v.wr.toFixed(1)}% win rate over ${v.n.toLocaleString()} games)` : " (no data)") +
    `. ${Math.round(v.laneP * 100)}% chance they're in your lane.`;
}

function chipsHtml(r) {
  if (!r.vs.length) return '<span class="no-enemies">Add enemies to see matchups</span>';
  return r.vs.map((v) => {
    const isLane = v.laneP >= 0.5;
    return `<span class="chip ${isLane ? "lane" : ""}" style="--chip:${chipColor(v.adv)}" title="${esc(matchupTip(r, v))}">
      <img src="${img(v.slug)}" alt="">${fmt(v.adv)}${isLane ? '<span class="lane-tag">LANE</span>' : ""}</span>`;
  }).join("");
}

function dangerHtml(r) {
  if (!r.danger) return "";
  const name = hero(r.danger.slug).name;
  return `<span class="danger" title="${esc(name)} counters ${esc(r.h.name)} hard (${fmt(r.danger.adv, 2)}%)">
    <svg viewBox="0 0 24 24" aria-hidden="true">${ICONS.warn}</svg>${esc(name)} ${fmt(r.danger.adv)}</span>`;
}

function scoreBar(score, max) {
  const w = Math.min(Math.abs(score) / max, 1) * 50;
  return `<div class="scorebar"><div class="track"><div class="fill ${score >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div><b class="${tone(score)}">${fmt(score)}</b></div>`;
}

const PART_LABELS = {
  counters: ["Counters", "Sum of matchup advantages vs. all enemies (sample-size adjusted)"],
  lane: ["Lane", "Advantage vs. likely lane opponents × lane weight"],
  meta: ["Meta", "(Position win rate − 50) × meta weight, sample-size adjusted, reduced for off-role picks"],
  comfort: ["Comfort", "Your experience on the hero × comfort weight"],
};

function breakdownHtml(r, compact) {
  const max = Math.max(3, ...Object.values(r.parts).map(Math.abs));
  const rows = Object.entries(PART_LABELS).map(([k, [label, tip]]) => {
    const v = r.parts[k];
    const w = Math.min(Math.abs(v) / max, 1) * 50;
    return `<div class="part" title="${esc(tip)}"><span>${label}</span>
      <div class="track"><div class="fill ${v >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>
      <b class="${tone(v)}">${fmt(v)}</b></div>`;
  }).join("");
  if (compact) return `<div class="breakdown compact">${rows}</div>`;

  // Per-enemy matchup bars; details live in tooltips.
  const advMax = Math.max(4, ...r.vs.map((v) => Math.abs(v.adv)));
  const matchups = r.vs.map((v) => {
    const w = Math.min(Math.abs(v.adv) / advMax, 1) * 50;
    const tip = `${hero(v.slug).name} · likely pos ${v.role} · ${Math.round(v.laneP * 100)}% in your lane\n` +
      (v.wr != null ? `${v.wr.toFixed(1)}% win rate over ${v.n.toLocaleString()} games (raw ${fmt(v.raw, 2)}%)` : "no matchup data");
    return `<div class="mu ${v.laneP >= 0.5 ? "lane" : ""}" title="${esc(tip)}">
      <span class="mu-hero"><img src="${img(v.slug)}" alt="${esc(hero(v.slug).name)}"><i>P${v.role}</i></span>
      <div class="track"><div class="fill ${v.adv >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>
      <b class="${tone(v.adv)}">${fmt(v.adv)}</b></div>`;
  }).join("");

  const ps = r.ps;
  const rankTxt = ps.rank === "all" ? "all ranks" : cap(ps.rank);
  const pills = [
    `<span class="pill-stat" title="${esc(ps.winRate != null
      ? `${ps.winRate.toFixed(2)}% over ${ps.matches.toLocaleString()} games (${rankTxt}), adjusted ${ps.adjusted.toFixed(2)}%`
      : `No ${rankTxt} data, using all ranks (adjusted ${ps.adjusted.toFixed(2)}%)`)}">
      <small>Pos ${state.position} WR</small><b class="${tone((ps.winRate ?? 50) - 50)}">${ps.winRate != null ? ps.winRate.toFixed(1) + "%" : "—"}</b></span>`,
    `<span class="pill-stat" title="${esc(`${r.share.toFixed(0)}% of ${r.h.name} games are pos ${state.position}`)}">
      <small>Role share</small><b>${r.share.toFixed(0)}%</b></span>`,
  ];
  if (r.share < ROLE_FULL_SHARE) {
    pills.push(`<span class="pill-stat warn" title="Off-role pick: specialists inflate niche win rates, so the meta term is scaled down">
      <small>Off-role</small><b>×${(r.share / ROLE_FULL_SHARE).toFixed(2)}</b></span>`);
  }
  if (state.player) {
    pills.push(`<span class="pill-stat" title="Your games on ${esc(r.h.name)} (${state.playerPeriod === "all" ? "all time" : "last 12 months"})">
      <small>You</small><b>${r.you.games ? `${r.you.games}g · ${Math.round((r.you.wins / r.you.games) * 100)}%` : "—"}</b></span>`);
  }
  return `<div class="detail"><div class="breakdown">${rows}</div>
    <div class="detail-info">${matchups ? `<div class="mus">${matchups}</div>` : ""}<div class="pill-stats">${pills.join("")}</div></div></div>`;
}

function youHtml(you) {
  if (!state.player) return "";
  if (!you.games) return '<span class="flat">—</span>';
  const wr = (you.wins / you.games) * 100;
  return `${you.games} g<small class="${tone(wr - 50, 2)}">${wr.toFixed(0)}% WR</small>`;
}

function posWrHtml(r) {
  const ps = r.ps;
  const val = ps.winRate != null ? `${ps.winRate.toFixed(1)}%` : "—";
  const trend = r.trend != null && Math.abs(r.trend) >= 0.3
    ? `<small class="trend ${tone(r.trend, 0)}" title="All-ranks change vs. ${new Date(state.data.trendSince).toLocaleDateString()}">${r.trend > 0 ? "▲" : "▼"}${Math.abs(r.trend).toFixed(1)}</small>`
    : "";
  return `<span class="${tone((ps.winRate ?? 50) - 50)}">${val}</span>${trend}`;
}

// ---------- main render ----------
function renderFeatured(rows) {
  $("featured").innerHTML = rows.slice(0, 3).map((r, i) => {
    const third = state.player
      ? `<div class="stat"><small>You</small><b>${r.you.games ? `${r.you.games}g · ${Math.round((r.you.wins / r.you.games) * 100)}%` : "—"}</b></div>`
      : `<div class="stat"><small>Plays pos ${state.position}</small><b>${r.share.toFixed(0)}%</b></div>`;
    return `<article class="feat rank-${i + 1}" data-slug="${r.h.slug}" tabindex="0" title="Click for build & breakdown" style="--img:url('${img(r.h.slug)}')">
      <div class="feat-top">
        <img src="${img(r.h.slug)}" alt="">
        <div><div class="feat-rank">#${i + 1} pick</div><div class="feat-name">${esc(r.h.name)}</div></div>
        <div class="feat-score"><b class="${tone(r.score)}">${fmt(r.score)}</b><small>score</small></div>
      </div>
      <div class="stats">
        <div class="stat"><small>Pos ${state.position} WR</small><b>${posWrHtml(r)}</b></div>
        <div class="stat" title="Matchup advantage vs likely lane opponents"><small>Lane est.</small><b class="${tone(r.laneRaw)}">${state.enemies.length ? fmt(r.laneRaw) : "—"}</b></div>
        ${third}
      </div>
      ${breakdownHtml(r, true)}
      ${dangerHtml(r)}
      <div class="chips">${chipsHtml(r)}</div>
    </article>`;
  }).join("");
}

function renderAvoid(all) {
  const el = $("avoid");
  if (!state.enemies.length) { el.hidden = true; return; }
  const worst = all
    .map((r) => ({ r, m: r.parts.counters + r.parts.lane }))
    .filter((x) => x.m < -1)
    .sort((a, b) => a.m - b.m)
    .slice(0, 6);
  el.hidden = worst.length === 0;
  el.innerHTML = `<span class="avoid-label"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS.warn}</svg>Avoid in this draft</span>` +
    worst.map(({ r, m }) => {
      const tip = r.vs.filter((v) => v.adv < -1).map((v) => `${hero(v.slug).name} ${fmt(v.adv)}`).join(", ");
      return `<span class="avoid-hero" title="${esc(`Matchup total ${fmt(m)}: ${tip}`)}"><img src="${img(r.h.slug)}" alt="">${esc(r.h.name)}<b>${fmt(m)}</b></span>`;
    }).join("");
}

function renderResults() {
  const enemies = enemyContext();
  renderSlots($("enemySlots"), state.enemies, 5, (s) => { state.enemies = state.enemies.filter((x) => x !== s); render(); }, enemies);

  const all = scoreAll(enemies).sort((a, b) => b.score - a.score);
  state.rows = new Map(all.map((r) => [r.h.slug, r]));
  const mine = state.player && state.poolMode === "mine";
  const rows = (mine ? all.filter((r) => r.you.games >= state.minGames) : all).slice(0, 30);
  const max = Math.max(4, ...rows.map((r) => Math.abs(r.score)));
  const rank = effectiveRank();
  $("resultsTitle").innerHTML = `Best <em>${posName(state.position)}</em> picks` +
    (state.enemies.length ? ` vs ${state.enemies.length} enem${state.enemies.length === 1 ? "y" : "ies"}` : "") +
    (rank !== "all" ? ` · ${cap(rank)}` : "") +
    (mine ? " · your heroes" : "");

  renderFeatured(rows);
  renderAvoid(all);
  const list = $("results");
  list.classList.toggle("no-player", !state.player);
  const head = `<div class="list-head"><span></span><span></span><span>Hero</span><span>Score</span>
    <span class="num" title="Win rate in this position (selected rank)">Pos WR</span>
    <span class="num" title="Matchup advantage vs. enemies likely in your lane">Lane</span>
    <span class="num col-you">You</span><span>Vs. enemies</span></div>`;
  list.innerHTML = rows.length > 3 ? head + rows.slice(3).map((r, i) => {
    const laneTip = (r.laneStats ? `Dotabuff ${POS_LANE[state.position]} lane: ${r.laneStats.winRate.toFixed(1)}% WR, ${r.laneStats.presence.toFixed(0)}% presence. ` : "") +
      "Estimated advantage vs. likely lane opponents.";
    return `<div class="row-wrap" role="listitem">
      <div class="row" data-slug="${r.h.slug}" tabindex="0" title="Click for build & breakdown">
        <span class="rank">${i + 4}</span>
        <img src="${img(r.h.slug)}" alt="" loading="lazy">
        <div class="hero-name">${esc(r.h.name)}<small>${r.share.toFixed(0)}% in pos ${state.position} · ${kfmt(r.ps.matches)} games</small>${dangerHtml(r)}</div>
        ${scoreBar(r.score, max)}
        <span class="num col-opt">${posWrHtml(r)}</span>
        <span class="num col-opt ${tone(r.laneRaw)}" title="${esc(laneTip)}">${state.enemies.length ? fmt(r.laneRaw) : "—"}</span>
        <span class="num col-opt col-you you">${youHtml(r.you)}</span>
        <div class="chips">${chipsHtml(r)}</div>
      </div>
    </div>`;
  }).join("") : "";
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = mine
    ? `None of your heroes (${state.minGames}+ games) fit this position — switch to "All heroes" or lower the thresholds in Tuning.`
    : "No heroes match — try lowering the minimum position share in Tuning.";
}

function renderPlayer() {
  const p = state.player;
  $("playerForm").hidden = !!p;
  $("playerInfo").hidden = !p;
  $("playerClear").hidden = !p;
  $("playerOptions").hidden = !p;
  if (p) {
    const pool = playerPool();
    const games = Object.values(pool).reduce((s, h) => s + h.games, 0);
    $("playerInfo").innerHTML = `${p.avatar ? `<img src="${esc(p.avatar)}" alt="">` : ""}
      <div><b>${esc(p.name)}</b><small>${p.rank ? cap(p.rank) + " · " : ""}${games.toLocaleString()} games · ${Object.keys(pool).length} heroes</small></div>`;
  }
  $("poolMode").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", b.dataset.mode === state.poolMode));
  $("playerPeriod").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", b.dataset.period === state.playerPeriod));
}

const SLIDERS = {
  minShare: (v) => v + "%",
  laneWeight: (v) => "×" + v,
  comfortWeight: (v) => "×" + v,
  minGames: (v) => v,
  metaWeight: (v) => "×" + v,
};

function render() {
  if (!state.data) return;
  renderPositions();
  renderRankSelect();
  renderPlayer();
  renderSlots($("excludeSlots"), state.excluded, null, (s) => { state.excluded = state.excluded.filter((x) => x !== s); render(); });
  $("excludeCount").textContent = state.excluded.length || "";
  for (const [k, f] of Object.entries(SLIDERS)) {
    $(k).value = state[k];
    $(k + "Out").textContent = f(state[k]);
  }
  renderResults();
  renderDrawer();
  save();
}

function setPosition(p) {
  state.position = p;
  render();
}

// ---------- hero drawer (breakdown + Dotabuff build) ----------
const asset = (path) => `/asset/${path}`;
const mmss = (t) => `${t < 0 ? "-" : ""}${Math.floor(Math.abs(t) / 60)}:${String(Math.abs(t) % 60).padStart(2, "0")}`;
let lastFocus = null;

function openDrawer(slug) {
  lastFocus = document.activeElement;
  state.drawer = slug;
  renderDrawer();
  $("drawerPanel").focus();
}

function closeDrawer() {
  state.drawer = null;
  renderDrawer();
  if (lastFocus) lastFocus.focus();
}

function loadBuild(slug, pos) {
  const key = `${slug}:${pos}`;
  if (!buildCache.has(key)) {
    buildCache.set(key, fetch(`/api/build?hero=${slug}&pos=${pos}`)
      .then(async (res) => {
        const body = await res.json();
        if (!res.ok) throw new Error(body.error || "Could not load build");
        return body;
      })
      .then((b) => { buildCache.set(key, b); if (state.drawer === slug) renderDrawer(); })
      .catch((err) => { buildCache.set(key, { error: err.message }); if (state.drawer === slug) renderDrawer(); }));
  }
  const v = buildCache.get(key);
  return v instanceof Promise ? null : v;
}

function itemIcon(it, extra = "") {
  return `<span class="item" title="${esc(it.name)}${it.share != null ? ` · in ${Math.round(it.share * 100)}% of builds` : ""}">
    <img src="${asset(it.icon)}" alt="${esc(it.name)}" loading="lazy">${extra}</span>`;
}

function buildHtml(b) {
  if (!b) return '<div class="build-loading"><span class="spinner"></span>Loading recent high-MMR builds from Dotabuff…</div>';
  if (b.error) return `<p class="player-error">${esc(b.error)}</p>`;
  if (!b.guides) return '<p class="sub">No Dotabuff guides for this hero right now.</p>';

  const core = b.items.filter((i) => i.core);
  const situational = b.items.filter((i) => !i.core);
  const pct = (x) => `${Math.round(x * 100)}%`;
  const source = `From ${b.guides} recent winning high-MMR games${b.avgMmr ? ` (~${b.avgMmr.toLocaleString()} MMR)` : ""}` +
    (b.matchedPosition ? ` played as pos ${b.position}.` : ` — too few pos ${b.position} games, showing all roles.`);

  // Skill grid: one row per ability (+ talents), one column per level.
  const levels = b.skillOrder.map((s) => s.level);
  const rows = [...b.abilities.map((a) => ({ key: a.name, label: a.name, icon: a.icon })),
    { key: "__talent", label: "Talents", icon: null }];
  const grid = `<div class="skill-grid" style="--cols:${levels.length}">
    <div class="sg-head"></div>${levels.map((l) => `<div class="sg-lvl">${l}</div>`).join("")}
    ${rows.map((row) => `<div class="sg-name" title="${esc(row.label)}">${row.icon ? `<img src="${asset(row.icon)}" alt="">` : '<span class="talent-ico">T</span>'}<span>${esc(row.label)}</span></div>` +
      b.skillOrder.map((s) => {
        const hit = row.key === "__talent" ? s.kind === "talent" : s.kind === "ability" && s.name === row.key;
        return `<div class="sg-cell ${hit ? "hit" : ""}" ${hit ? `title="${esc(`Level ${s.level}: ${s.name} (${pct(s.share)} of games)`)}"` : ""}>${hit ? s.level : ""}</div>`;
      }).join("")).join("")}
  </div>`;

  return `<p class="sub">${esc(source)}</p>
    <h4>Starting items</h4>
    <div class="items">${b.starting.map((i) => itemIcon(i, `<small>${pct(i.share)}</small>`)).join("") || '<span class="sub">—</span>'}</div>
    <h4>Core items <span class="sub">in typical buy order</span></h4>
    <div class="items timeline">${core.map((i) => itemIcon(i, `<small>${i.time != null ? mmss(i.time) : ""}</small>`)).join('<span class="arrow">›</span>') || '<span class="sub">—</span>'}</div>
    ${situational.length ? `<h4>Situational</h4><div class="items">${situational.map((i) => itemIcon(i, `<small>${pct(i.share)}</small>`)).join("")}</div>` : ""}
    ${b.neutrals.length ? `<h4>Neutral items</h4><div class="items">${b.neutrals.map((i) => itemIcon(i, `<small>${pct(i.share)}</small>`)).join("")}</div>` : ""}
    <h4>Skill order</h4>
    <div class="grid-scroll">${grid}</div>
    ${b.talents.length ? `<h4>Popular talents</h4><ul class="talents">${b.talents.map((t) => `<li><span>${esc(t.name)}</span><b>${pct(t.share)}</b></li>`).join("")}</ul>` : ""}
    <h4>Recent games</h4>
    <ul class="matches">${b.matches.slice(0, 8).map((m) => `<li>
      <a href="https://www.dotabuff.com/matches/${m.matchId}" target="_blank" rel="noopener">${esc(m.player || "Anonymous")}</a>
      <span class="sub">${m.mmr ? `~${m.mmr.toLocaleString()} · ` : ""}${esc(m.date || "")}</span>
      <span class="mini-items">${m.final.map((i) => `<img src="${asset(i.icon)}" alt="${esc(i.name)}" title="${esc(i.name)}" loading="lazy">`).join("")}</span>
    </li>`).join("")}</ul>`;
}

function renderDrawer() {
  const open = !!(state.drawer && state.data && hero(state.drawer));
  $("drawer").hidden = !open;
  document.body.classList.toggle("drawer-open", open);
  if (!open) return;
  const h = hero(state.drawer);
  const r = state.rows.get(state.drawer);
  const build = loadBuild(h.slug, state.position);
  $("drawerBody").innerHTML = `
    <header class="drawer-head" style="--img:url('${img(h.slug)}')">
      <img src="${img(h.slug)}" alt="">
      <div><div class="feat-rank">Pos ${state.position} · ${posName(state.position)}</div><h3 id="drawerTitle">${esc(h.name)}</h3></div>
      ${r ? `<div class="feat-score"><b class="${tone(r.score)}">${fmt(r.score)}</b><small>score</small></div>` : ""}
      <button class="icon-btn close" data-close aria-label="Close">✕</button>
    </header>
    <section class="drawer-sec">
      <h4>Why this score</h4>
      ${r ? breakdownHtml(r, false) + dangerHtml(r) : '<p class="sub">This hero isn\'t in the current suggestions (filtered out or already picked).</p>'}
    </section>
    <section class="drawer-sec">
      <h4 class="sec-title">Build</h4>
      ${buildHtml(build)}
    </section>`;
}

// ---------- status pill ----------
function setStatus(kind, text) {
  $("updated").className = "pill " + kind;
  $("updated").querySelector("span").textContent = text;
  $("refresh").classList.toggle("spinning", kind === "busy");
}
function ago(iso) {
  const h = (Date.now() - new Date(iso)) / 36e5;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

// ---------- data ----------
async function loadData() {
  const res = await fetch("/api/data");
  if (!res.ok) {
    setStatus("busy", "First scrape in progress…");
    setTimeout(loadData, 5000);
    return;
  }
  state.data = await res.json();
  state.heroes = Object.values(state.data.heroes).sort((a, b) => a.name.localeCompare(b.name));
  const known = (s) => s in state.data.heroes;
  state.enemies = state.enemies.filter(known).slice(0, 5);
  state.excluded = state.excluded.filter(known);
  if (state.rank !== "auto" && state.rank !== "all" && !state.data.ranks.includes(state.rank)) state.rank = "auto";
  const stale = Date.now() - new Date(state.data.updatedAt) > 26 * 36e5;
  const patch = state.data.patch ? ` · patch ${state.data.patch}` : "";
  setStatus(stale ? "stale" : "fresh", `Dotabuff · ${state.data.period}${patch} · updated ${ago(state.data.updatedAt)}`);
  $("updated").title = `${new Date(state.data.updatedAt).toLocaleString()} · ${state.data.snapshots} snapshot(s) kept`;
  render();
}

async function loadPlayer(id) {
  const btn = $("playerForm").querySelector("button");
  btn.disabled = true;
  $("playerError").hidden = true;
  try {
    const res = await fetch("/api/player?id=" + encodeURIComponent(id));
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "Could not load player");
    state.player = body;
    state.playerId = String(body.accountId);
  } catch (err) {
    $("playerError").textContent = err.message;
    $("playerError").hidden = false;
  } finally {
    btn.disabled = false;
    render();
  }
}

async function pollRefresh(since) {
  const s = await (await fetch("/api/status")).json();
  if (!s.refreshing && s.updatedAt !== since) return loadData();
  if (!s.refreshing && s.lastRun && s.lastRun.status === "error") {
    return setStatus("error", "Refresh failed: " + (s.lastRun.detail || "unknown error"));
  }
  setTimeout(() => pollRefresh(since), 2000);
}

// ---------- wire up ----------
function segment(id, attr, key) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest(`button[data-${attr}]`);
    if (b) { state[key] = b.dataset[attr]; render(); }
  });
}

function init() {
  restore();
  renderPositions();

  $("positions").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-pos]");
    if (b) setPosition(+b.dataset.pos);
  });
  $("rank").addEventListener("change", (e) => { state.rank = e.target.value; render(); });

  const taken = () => new Set([...state.enemies, ...state.excluded]);
  attachSearch($("search"), $("suggest"), (slug) => {
    if (state.enemies.length < 5) state.enemies.push(slug);
    render();
  }, taken, (e) => {
    if (e.key === "Backspace" && state.enemies.length) { state.enemies.pop(); render(); return true; }
    if (/^[1-5]$/.test(e.key)) { setPosition(+e.key); return true; }
    return false;
  });
  attachSearch($("excludeSearch"), $("excludeSuggest"), (slug) => {
    state.excluded.push(slug);
    render();
  }, taken, (e) => {
    if (e.key === "Backspace" && state.excluded.length) { state.excluded.pop(); render(); return true; }
    return false;
  });

  for (const k of Object.keys(SLIDERS)) {
    $(k).addEventListener("input", (e) => { state[k] = +e.target.value; render(); });
  }
  $("clear").addEventListener("click", () => { state.enemies = []; render(); $("search").focus(); });
  $("playerForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("playerInput").value.trim();
    if (v) loadPlayer(v);
  });
  $("playerClear").addEventListener("click", () => {
    state.player = null; state.playerId = ""; state.poolMode = "all";
    $("playerInput").value = "";
    render();
  });
  segment("poolMode", "mode", "poolMode");
  segment("playerPeriod", "period", "playerPeriod");

  for (const id of ["results", "featured"]) {
    $(id).addEventListener("click", (e) => {
      const el = e.target.closest("[data-slug]");
      if (el) openDrawer(el.dataset.slug);
    });
    $(id).addEventListener("keydown", (e) => {
      const el = e.target.closest("[data-slug]");
      if (el && (e.key === "Enter" || e.key === " ")) { openDrawer(el.dataset.slug); e.preventDefault(); }
    });
  }
  $("drawer").addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) closeDrawer();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.drawer) closeDrawer();
  });

  $("refresh").addEventListener("click", async () => {
    const since = state.data && state.data.updatedAt;
    setStatus("busy", "Refreshing from Dotabuff…");
    await fetch("/api/refresh", { method: "POST" });
    setTimeout(() => pollRefresh(since), 1500);
  });

  $("search").focus();
  loadData().then(() => { if (state.playerId) loadPlayer(state.playerId); });
}

init();
