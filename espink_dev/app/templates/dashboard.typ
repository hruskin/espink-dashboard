// Dashboard pro e-paper (Typst). Rozměry odpovídají dřívější HTML šabloně (Chromium).
// Server před šablonu vloží: #let data = json.decode("…") s klíči view, updated,
// updated_time, next_wake a icons (název ikony MDI -> kód znaku). Veškerý text jde
// do šablony jako data (řetězce), nikdy jako kód Typstu.
// 1 pt = 1 px (PNG se překládá s --ppi 72).

#let view = data.view
#let ICONS = data.icons
#let RED = rgb("#e00000")
// MDI v záložních fontech: emoji z tabulky (emoji.py) jsou v textu jako znaky ikon
#let SANS = ("Inter", "Material Design Icons", "Noto Emoji")
#let DISP = ("Inter Display", "Material Design Icons", "Noto Emoji")
#let COND = ("RobotoCondensed", "Material Design Icons", "Noto Emoji")
#let MDI = "Material Design Icons"

#set page(width: 480pt, height: 800pt, margin: 0pt, fill: white)
// výška řádku jako v CSS (line-height: normal = ascender + descender, bez mezery)
#set text(font: SANS, size: 16pt, weight: 500, fill: black, lang: "cs", hyphenate: false,
          top-edge: "ascender", bottom-edge: "descender", number-width: "tabular")
#set par(leading: 0pt, spacing: 0pt)
#set block(spacing: 0pt)

#let pt(n) = n * 1pt
#let clamp(lo, x, hi) = calc.min(hi, calc.max(lo, x)) * 1pt
#let txt(s) = if s == none { "" } else { str(s) }
// bez zalomení (CSS white-space: nowrap): mezery nezlomitelné
#let nb(s) = txt(s).replace(" ", "\u{a0}")
#let sp(n) = box(width: n * 1pt)
// pravdivost jako v Jinja/Pythonu: none, "", false, 0 a prázdný seznam = nic
#let has(x) = not (x == none or x == "" or x == false or x == 0 or x == ())

// ikona Material Design Icons; neznámý název = nic
#let icon(name, size: 20pt, fill: auto) = {
  let cp = if not has(name) { none } else { ICONS.at(str(name), default: none) }
  if cp != none {
    let t = text(font: MDI, size: size, top-edge: 0.875em, bottom-edge: -0.125em, str.from-unicode(cp))
    if fill == auto { t } else { text(fill: fill, t) }
  }
}

// text zkrácený výpustkou na dostupnou šířku (CSS text-overflow: ellipsis)
#let ell(s, ..st) = layout(size => {
  let s = txt(s)
  let t(x) = text(..st, x)
  if measure(t(s)).width <= size.width { return t(s) }
  let cl = s.clusters()
  let lo = 0
  let hi = cl.len()
  while lo < hi {
    let mid = calc.quo(lo + hi + 1, 2)
    if measure(t(cl.slice(0, mid).join() + "…")).width <= size.width { lo = mid } else { hi = mid - 1 }
  }
  t(if lo == 0 { "…" } else { cl.slice(0, lo).join().trim() + "…" })
})

// text zalomený nejvýš do výšky h; co se nevejde, nahradí výpustka na posledním řádku
#let ell-lines(s, h, ..st) = layout(size => {
  let s = txt(s)
  let t(x) = text(..st, x)
  let fits(x) = measure(block(width: size.width, t(x))).height <= h + 0.5pt
  if fits(s) { return t(s) }
  let cl = s.clusters()
  let lo = 0
  let hi = cl.len()
  while lo < hi {
    let mid = calc.quo(lo + hi + 1, 2)
    if fits(cl.slice(0, mid).join() + "…") { lo = mid } else { hi = mid - 1 }
  }
  t(if lo == 0 { "…" } else { cl.slice(0, lo).join().trim() + "…" })
})

// řádek pevné výšky s obsahem svisle uprostřed (CSS line-height / flex align-items: center)
#let line(h, body, x: left) = box(width: 100%, height: pt(h), clip: true, align(x + horizon, body))
// prvky vedle sebe bez zalomení, svisle na střed (CSS display: flex; align-items: center)
#let hrow(items, gap: 0pt, h: auto) = stack(dir: ltr, spacing: gap, ..items.filter(c => c != none).map(c => box(height: h, align(horizon, c))))

// ── Počasí teď (záhlaví i samostatný blok) ───────────────────────────
#let now(w, x: right, small: false, narrow: false, cw: 0) = {
  let top = ()
  top.push(icon(w.icon, size: if small { clamp(22, cw * 0.22, 40) } else { 50pt }))
  if has(w.min) and has(w.max) and not small and not narrow {
    top.push(box(inset: (right: 2pt), align(right, stack(dir: ttb,
      ..if has(w.at("minmax_label", default: none)) {
        (box(height: 13pt, align(horizon, text(size: 12pt, weight: 800, upper(txt(w.minmax_label))))),)
      } else { () },
      box(height: 20pt, align(right + horizon, text(size: 17pt, weight: 800, tracking: -0.5pt, txt(w.max) + "°"))),
      box(height: 20pt, align(right + horizon, text(size: 17pt, weight: 600, tracking: -0.5pt, txt(w.min) + "°"))),
    ))))
  }
  let t = txt(w.temp)
  let tsize = if small { clamp(20, cw * 0.26, 46) } else if narrow { 38pt } else if t.len() > 4 { 36pt } else { 46pt }
  top.push(box(height: tsize, align(horizon, text(font: DISP, size: tsize,
    weight: if w.at("temp_bold", default: true) { 800 } else { 400 }, tracking: if small { -1pt } else { -1.5pt }, (if t == "" { "--" } else { t }) + "°"))))
  let isize = if small { clamp(22, cw * 0.22, 40) } else { 50pt }
  let row = hrow(top, gap: if small { 2pt } else { 4pt }, h: if small { calc.max(isize, tsize) } else { 54pt })
  if small { box(width: 100%, clip: true, align(x, row)) } else { line(54, row, x: x) }
  if not small { v(2pt) }
  let fs = if small { 15pt } else if narrow { 16pt } else { 18pt }
  let isz = if small { 18pt } else { 26pt }
  let gap = if small { 4pt } else if narrow { 6pt } else { 10pt }
  let item(ic, s) = hrow((icon(ic, size: isz), text(size: fs, weight: 600, nb(s))), gap: 3pt, h: 24pt)
  let sub(items) = if items.len() > 0 { line(24, hrow(items, gap: gap, h: 24pt), x: x) }
  let s1 = ()
  if has(w.humidity) { s1.push(item("water-percent", txt(w.humidity) + " %")) }
  if has(w.rain) and not small { s1.push(item("weather-pouring", txt(w.rain) + " mm")) }
  sub(s1)
  if not small {
    let s2 = ()
    if has(w.indoor) { s2.push(item("home-thermometer-outline", txt(w.indoor) + "°")) }
    if has(w.pressure) { s2.push(item("gauge", txt(w.pressure) + " hPa")) }
    sub(s2)
  }
}

// ── Bloky ────────────────────────────────────────────────────────────
#let header(b, cw) = {
  let hol = has(b.holiday)
  let daynum = box(height: 72.52pt, align(horizon, text(font: DISP, size: 98pt, weight: 400, tracking: -7pt,
    fill: if hol { RED } else { black }, txt(b.day))))
  let datetext = {
    v(4pt)
    box(height: 29.4pt, align(horizon, text(font: DISP, size: 28pt, weight: 800, fill: if hol { RED } else { black }, txt(b.weekday))))
    linebreak()
    v(3pt)
    if has(b.week) {
      // „října · 43. týden“; když se nevejde, „října · 43. t.“, pak úzké mezery a menší písmo
      layout(size => {
        let m = txt(b.month)
        let w = str(b.week)
        let tries = (m + " · " + w + ". týden", m + " · " + w + ". t.", m + "\u{2009}·\u{2009}" + w + ".\u{2009}t.")
        let fits(x, fs) = measure(text(size: fs, weight: 600, nb(x))).width <= size.width
        let (s, fs) = (tries.last(), 13pt)
        for x in tries { if fits(x, 18pt) { (s, fs) = (x, 18pt); break } }
        if fs == 13pt { for f in (17, 16, 15, 14) { if fits(tries.last(), f * 1pt) { fs = f * 1pt; break } } }
        text(size: fs, weight: 600, nb(s))
      })
    } else { text(size: 18pt, weight: 600, txt(b.month)) }
    if has(b.nameday) {
      v(8pt)
      grid(columns: (auto, 1fr), column-gutter: 1pt, align: horizon, icon("flower-tulip-outline", size: 20pt),
        ell(b.nameday, size: 17pt, weight: 600, tracking: -0.2pt))
    }
  }
  let date = box(width: 100%, clip: true, grid(columns: (auto, 1fr), pad(left: -4pt, right: 12pt, top: 12pt, daynum), datetext))
  let now-w = if cw <= 320 { none } else if cw <= 420 { 150pt } else { 212pt }
  pad(x: 16pt, top: pt(b.pad_top), {
    if has(b.weather) and now-w != none {
      grid(columns: (1fr, 6pt, now-w), date, [], now(b.weather, narrow: cw <= 420))
    } else { date }
  })
  let line-text = if has(b.alert) { b.alert } else { b.holiday }
  if has(line-text) {
    let msg = ell(line-text, size: 19pt, weight: 800, fill: RED)
    place(bottom + left, dx: 16pt, dy: -3pt, box(width: pt(cw), height: 24pt,
      if has(b.alert) and has(b.alert_icon) {
        grid(columns: (auto, 1fr), column-gutter: 4pt, align: horizon, icon(b.alert_icon, size: 22pt, fill: RED), msg)
      } else { msg }))
  }
}

#let weather-now(b, cw) = pad(x: 12pt, align(center + horizon, now(b.weather, x: center, small: cw <= 210, cw: cw)))

#let departures(b, cw) = {
  let narrow = cw <= 330
  pad(x: 16pt, top: 8pt, {
    line(26, grid(columns: (auto, 1fr, auto), column-gutter: 6pt, align: horizon,
      icon("bus", size: 22pt),
      ell(upper(txt(if has(b.stop) { b.stop } else { "Odjezdy" })), size: 17pt, weight: 800, tracking: 0.5pt),
      if not narrow { text(size: 17pt, weight: 800, "stav " + data.updated_time) }))
    if has(b.error) { pad(top: 8pt, text(size: 19pt, weight: 600, txt(b.error))) }
    let (tw, ts, dw, ds, rw, rh, rs, hs, gap) = if narrow { (58, 21, 30, 15, 40, 22, 15, 18, 5) } else { (72, 25, 32, 19, 48, 25, 18, 22, 8) }
    for x in b.rows {
      let time = text(size: pt(ts), weight: 800, txt(x.time))
      line(30, grid(columns: (pt(tw), pt(dw), pt(rw), 1fr, if narrow { 0pt } else { 24pt }),
        column-gutter: (pt(gap), pt(gap), pt(gap), if narrow { 0pt } else { pt(gap) }), align: horizon,
        if x.canceled { strike(stroke: 3pt, time) } else { time },
        move(dx: if narrow { 0pt } else { -2pt }, text(size: pt(ds), weight: 800, fill: RED, txt(x.delay))),
        box(width: pt(rw), height: pt(rh), fill: black, radius: 4pt,
          align(center + horizon, text(size: pt(rs), weight: 800, fill: white, txt(x.route)))),
        if x.canceled { ell("ZRUŠENO", font: COND, size: pt(hs), weight: 700, fill: RED) }
        else { ell(x.headsign, font: COND, size: pt(hs), weight: 600) },
        if not narrow and has(x.platform) {
          box(width: 24pt, height: 24pt, stroke: (paint: black, thickness: 2pt), radius: 4pt, inset: 0pt,
            align(center + horizon, text(size: 16pt, weight: 800, txt(x.platform))))
        },
      ))
    }
    if has(b.disruptions) {
      box(width: 100%, height: 30pt, pad(top: 5pt, grid(columns: (auto, 1fr), column-gutter: 4pt, align: horizon,
        icon("alert", size: 17pt, fill: RED), ell(b.disruptions.text, size: 17pt, weight: 700, fill: RED))))
    }
  })
}

#let agenda(b, cw) = {
  let (dlw, dls, tw, ts, ss, gap, ci) = if cw <= 280 { (38, 13, 44, 14, 17, 4, false) }
    else if cw <= 330 { (46, 14, 44, 16, 18, 5, b.icons) } else { (58, 17, 50, 18, 21, 8, b.icons) }
  let tags = cw > 330
  // účaří řádku jako v CSS (align-items: baseline): nejvyšší ascender v řádku
  let base = calc.max(0.928 * ss, 0.969 * ts, 0.969 * dls)
  let at-base(body) = text(top-edge: pt(base), bottom-edge: pt(base - 29), body)
  pad(x: 16pt, top: 6pt, {
    let first = true
    for day in b.days {
      if day.rows.len() > 0 {
        if not first { box(width: 100%, height: 1pt, fill: black) }
        first = false
        for (i, e) in day.rows.enumerate() {
          let fill = if e.red { RED } else { black }
          let cols = (pt(dlw), pt(tw))
          let cells = (
            at-base(text(size: pt(dls), weight: 800, fill: if day.holiday_day { RED } else { black },
              if i == 0 { nb(day.label) } else { "" })),
            at-base(text(size: pt(ts), weight: 700, fill: fill, nb(e.time))),
          )
          if ci {
            cols.push(18pt)
            cells.push(box(height: 29pt, align(center + horizon, move(dx: -2pt, icon(e.icon, size: 23pt, fill: fill)))))
          }
          cols.push(1fr)
          let sw = if e.red or e.at("allday", default: false) { 700 } else { 500 }
          // „celý text“ (kalendář s wrap): zalomit do tolika řádků, kolik spočítal model
          let lines = e.at("lines", default: 1)
          let line-h = calc.round(ss * 1.15)
          cells.push(if e.at("wrap", default: false) {
            set par(linebreaks: "simple", leading: pt(line-h - 29))
            at-base(ell-lines(e.summary, pt(29 + (lines - 1) * line-h), font: COND, size: pt(ss), weight: sw, fill: fill))
          } else { at-base(ell(e.summary, font: COND, size: pt(ss), weight: sw, fill: fill)) })
          if tags and has(e.label) {
            cols.push(auto)
            cells.push(box(stroke: 2pt + fill, radius: 4pt, inset: (x: 6pt), height: 24pt,
              align(horizon, text(size: 15pt, weight: 700, fill: fill, txt(e.label)))))
          }
          // výška podle skutečného zalomení (model počítá s rezervou – případné místo navíc zůstane dole)
          box(width: 100%, height: if e.at("wrap", default: false) { auto } else { 29pt }, clip: true,
            grid(columns: cols, column-gutter: pt(gap), align: top, ..cells))
        }
      }
    }
    if b.empty { pad(top: 8pt, text(size: 19pt, weight: 600, "Žádné události v příštích " + str(b.range) + " dnech")) }
    if b.hidden > 0 {
      line(22, pad(top: 2pt, text(size: 15pt, weight: 700, "+ " + str(b.hidden) + " další" + if b.hidden >= 5 { "ch" } else { "" } + " …")), x: right)
    }
  })
}

// ── Jídelníček: zkracování po slovech/položkách a zalamování s rozpočtem řádků ──
// výpustka na hranici položky („Veka, máslo …“) nebo slova („masem a…“), nikdy uprostřed slova
#let cut(s, w, st) = {
  let t(x) = text(..st, x)
  let fits(x) = measure(t(x)).width <= w
  if fits(s) { return s }
  let parts = s.split(", ")
  for k in range(parts.len() - 1, 0, step: -1) {
    let c = parts.slice(0, k).join(", ") + " …"
    if fits(c) { if measure(t(c)).width >= 0.55 * w { return c } else { break } }
  }
  let ws = s.split(" ")
  // před výpustkou nenechat předložku/spojku („masem a…“ -> „masem…“)
  let weak = ("a", "i", "s", "se", "z", "ze", "v", "ve", "na", "k", "ke", "o", "do", "s.", "+")
  for k in range(ws.len() - 1, 0, step: -1) {
    let ww = ws.slice(0, k)
    while ww.len() > 1 and lower(ww.last()) in weak { ww = ww.slice(0, -1) }
    let c = ww.join(" ").trim(",", at: end).trim() + "…"
    if fits(c) { return c }
  }
  "…"
}
// rozdělí text na řádky (po slovech); víc než n => poslední řádek zkrácený
#let wrap(s, w, n, st) = {
  let t(x) = text(..st, x)
  let fits(x) = measure(t(x)).width <= w
  // jednopísmenné předložky a spojky nezůstanou na konci řádku (vlnka)
  let ws = ()
  for x in txt(s).split(" ") {
    if ws.len() > 0 and lower(ws.last()) in ("a", "i", "s", "v", "k", "z", "o", "u") { ws.at(-1) = ws.last() + "\u{a0}" + x }
    else { ws.push(x) }
  }
  let lines = ()
  let cur = ""
  for x in ws {
    let c = if cur == "" { x } else { cur + " " + x }
    if fits(c) or cur == "" { cur = c } else { lines.push(cur); cur = x }
  }
  if cur != "" { lines.push(cur) }
  if lines.len() <= n { return lines }
  let head = lines.slice(0, n - 1)
  head + (cut(lines.slice(n - 1).join(" "), w, st),)
}
#let needed(s, w, st) = wrap(s, w, 99, st).len()

// rows: pole (text, ikona, styl, výška řádku, priorita zalomení, max řádků)
#let menu-rows(rows, avail, w) = context {
  let n = rows.map(r => 1)
  let used = rows.map(r => r.h).sum(default: 0pt)
  let order = range(rows.len()).sorted(key: i => rows.at(i).prio)
  for i in order {
    let r = rows.at(i)
    let need = calc.min(r.maxl, needed(r.s, w, r.st))
    while n.at(i) < need and used + r.h2 <= avail { n.at(i) += 1; used += r.h2 }
  }
  for (i, r) in rows.enumerate() {
    for (j, l) in wrap(r.s, w, n.at(i), r.st).enumerate() {
      let h = if j == 0 { r.h } else { r.h2 }
      box(width: 100%, height: h, clip: true, grid(columns: (28pt, 1fr), column-gutter: 4pt, align: horizon,
        if j == 0 { r.ic } else { [] }, text(..r.st, l)))
    }
  }
}

#let menu-head(b) = line(26, grid(columns: (1fr, auto), column-gutter: 8pt, align: horizon,
    ell(upper(txt(b.title)), size: 17pt, weight: 800, tracking: 0.5pt),
    text(size: 17pt, weight: 800, tracking: 0.5pt, upper(txt(b.when)))))

#let menu(b, cw, ch) = pad(x: 16pt, top: 8pt, {
  menu-head(b)
  if has(b.missing) { pad(top: 10pt, text(font: COND, size: 22pt, weight: 600, txt(b.missing))) } else {
    v(2pt)
    let w = pt(cw) - 32pt - 32pt
    let rows = ()
    for x in b.items {
      let lunch = lower(txt(x.label)) == "oběd"
      for (i, d) in x.lines.enumerate() {
        rows.push(if lunch {
          (s: d, ic: if i == 0 { icon(x.icon, size: 23pt) } else { [] }, h: 29pt, h2: 24pt, prio: if i > 0 { 0 } else { 1 }, maxl: 2,
           st: (font: COND, size: 23pt, weight: if i > 0 { 700 } else { 500 }))
        } else {
          (s: d, ic: icon(x.icon, size: 19pt), h: 23pt, h2: 21pt, prio: 5, maxl: 2, st: (font: COND, size: 19pt, weight: 500))
        })
      }
    }
    menu-rows(rows, pt(ch) - 36pt, w)
  }
})

#let bedtime(b, cw, ch) = pad(x: 16pt, top: 8pt, {
  line(26, grid(columns: (1fr, auto), column-gutter: 8pt, align: horizon,
    ell(upper(txt(b.title)), size: 17pt, weight: 800, tracking: 0.5pt),
    if has(b.when) { text(size: 17pt, weight: 800, tracking: 0.5pt, upper(txt(b.when))) }))
  if has(b.missing) { pad(top: 4pt, text(font: COND, size: 22pt, weight: 600, txt(b.missing))) } else {
    // „Seriál: Epizoda“ – seriál tučně; delší text se zalomí, kolik řádků se vejde
    let n = calc.max(1, calc.floor((ch - 36) / 29))
    pad(top: 2pt, context {
      let st = (font: COND, size: 23pt, weight: 500)
      let w = pt(cw) - 32pt
      let head = if has(b.series) { txt(b.series) + ": " } else { "" }
      // seriál je tučně (širší) – zalamovat s rezervou o rozdíl šířky
      let extra = measure(text(..st, weight: 700, head)).width - measure(text(..st, head)).width
      let lines = wrap(head + txt(b.episode), w - extra, n, st)
      let hc = head.clusters().len()
      for (i, l) in lines.enumerate() {
        // wrap() mohl dát za předložku nezlomitelnou mezeru – porovnávat bez ní
        let lc = l.clusters()
        let bold = i == 0 and hc > 0 and lc.len() >= hc and lc.slice(0, hc).join().replace("\u{a0}", " ") == head
        box(width: 100%, height: if i == 0 { 29pt } else { 26pt }, clip: true, align(horizon,
          if bold { text(..st, weight: 700, lc.slice(0, hc).join()); text(..st, lc.slice(hc).join()) }
          else { text(..st, l) }))
      }
    })
  }
})

#let forecast(b, cw) = {
  let narrow = cw <= 330
  if b.days.len() > 0 {
    grid(columns: (1fr,) * b.days.len(), ..b.days.map(f => {
      let fs = if narrow { 16pt } else { 18pt }
      let hi = if f.max == none { "--" } else { txt(f.max) }
      let lo = if f.min == none { "--" } else { txt(f.min) }
      // řádky pod sebou jako bloky (výška = písmo, jako CSS line-height: normal)
      let rows = (
        { text(size: 17pt, weight: 800, txt(f.label)); if not narrow { text(size: 15pt, weight: 700, " " + txt(f.date)) } },
        box(height: 38pt, icon(f.icon, size: 36pt)),
      )
      if b.stack {
        rows += (text(size: fs, weight: 800, hi + "°"), text(size: fs, weight: 600, lo + "°"))
      } else {
        rows.push({ text(size: fs, weight: 800, hi + "° "); text(size: fs, weight: 600, "/ " + lo + "°") })
        if has(f.precip) { rows.push(text(size: 15pt, weight: 700, txt(f.precip))) }
      }
      pad(top: 6pt, stack(dir: ttb, ..rows.map(r => align(center, r))))
    }))
  }
}

#let footer(b, cw) = {
  // jako CSS white-space: nowrap – položky se nezalamují, co se nevejde, ořízne okraj bloku
  let fit(ic, s) = (icon(ic, size: 20pt), sp(4), text(size: 14pt, weight: 600, nb(s)))
  let num(s, fill: black) = text(size: 15pt, weight: 700, fill: fill, nb(s))
  let groups = ()
  if b.updated and cw > 200 { groups.push(fit("update", data.updated)) }
  if b.next and has(data.next_wake) and cw > 330 { groups.push(fit("timer-sand", "za " + txt(data.next_wake))) }
  if b.week and cw > 440 { groups.push(fit("calendar-week", txt(b.week_label))) }
  let ent(it) = {
    let out = ()
    if has(it.icon) { out += (icon(it.icon, size: 20pt), sp(4)) }
    if has(it.label) { out += (text(size: 14pt, weight: 600, nb(it.label)), sp(4)) }
    out.push(num(txt(it.value) + if has(it.unit) { " " + txt(it.unit) } else { "" }))
    // CSS .f-ent { max-width: 45%; overflow: hidden }
    let r = stack(dir: ltr, ..out.map(c => box(height: 24pt, align(horizon, c))))
    (layout(size => box(width: calc.min(measure(r).width, cw * 0.45pt), height: 24pt, clip: true, r)),)
  }
  for it in b.items.filter(it => it.side == "left") { groups.push(ent(it)) }
  let right = b.items.filter(it => it.side == "right").map(ent)
  let dev = view.device
  let tail = ()
  if has(dev.at("wifi_icon", default: none)) { tail += (icon(dev.wifi_icon, size: 20pt), sp(4), text(size: 14pt, weight: 600, nb("slabá WiFi"))) }
  if b.battery and dev.at("battery", default: none) != none {
    let f = if dev.battery_low { RED } else { black }
    if tail.len() > 0 { tail.push(sp(4)) }
    tail += (icon(dev.battery_icon, size: 20pt, fill: f), sp(6), num(str(dev.battery) + " %", fill: f))
  }
  let join(gs) = {
    let out = ()
    for (i, g) in gs.enumerate() { if i > 0 { out.push(sp(16)) }; out += g }
    out
  }
  let row(items) = stack(dir: ltr, ..items.map(c => box(height: 24pt, align(horizon, c))))
  let l = row(join(groups))
  let r = row(join(right) + if right.len() > 0 and tail.len() > 0 { (sp(4),) } else { () } + tail)
  pad(x: 16pt, line(b.inner_h, layout(size => {
    let gap = calc.max(0pt, size.width - measure(l).width - measure(r).width)
    stack(dir: ltr, l, h(gap), r)
  })))
}

#let value(b, cw, ch) = {
  let narrow = cw <= 359
  let fi = if narrow { clamp(14, ch * 0.26, 28) } else { clamp(16, ch * 0.6, 64) }
  let fl = if narrow { clamp(12, ch * 0.24, 22) } else { clamp(14, ch * 0.34, 30) }
  let fv = if b.text { if narrow { clamp(12, calc.min(ch * 0.3, cw * 0.13), 40) } else { clamp(14, calc.min(ch * 0.42, cw * 0.09), 48) } }
    else if narrow and not has(b.label) { clamp(14, calc.min(ch * 0.64, cw * 0.22), 80) }
    else if narrow { clamp(14, calc.min(ch * 0.5, cw * 0.24), 80) }
    else { clamp(16, calc.min(ch * 0.66, cw * 0.13), 80) }
  let fill = if b.red { RED } else { black }
  let unit = if has(b.unit) { text(size: 0.55em, weight: 700, tracking: 0pt, " " + txt(b.unit)) }
  let val = if b.text { ell(b.value, font: COND, size: fv, weight: 800, fill: fill) }
    else { text(size: fv, weight: 800, tracking: -0.5pt, fill: fill, txt(b.value) + unit) }
  let ic = if has(b.icon) { icon(b.icon, size: fi) }
  pad(x: 12pt, box(width: 100%, height: 100%, {
    if not has(b.label) {
      align(center + horizon, grid(columns: if ic != none { 2 } else { 1 }, column-gutter: if narrow { 4pt } else { 8pt },
        align: horizon, ..(ic, if b.text { box(width: pt(cw) - if ic != none { fi + 8pt } else { 0pt }, align(center, val)) } else { val }).filter(x => x != none)))
    } else if narrow {
      align(horizon, stack(dir: ttb,
        grid(columns: if ic != none { (auto, 1fr) } else { (1fr,) }, column-gutter: 4pt, align: horizon,
          ..(ic, ell(b.label, font: COND, size: fl, weight: 600)).filter(x => x != none)),
        box(width: 100%, height: fv, align(center + horizon, val))))
    } else {
      layout(size => {
        let natural = measure(if b.text { text(font: COND, size: fv, weight: 800, txt(b.value)) } else { val }).width
        let rest = size.width - if ic != none { fi + 8pt } else { 0pt } - 8pt
        align(horizon, grid(columns: if ic != none { (auto, 1fr, calc.min(natural, rest)) } else { (1fr, calc.min(natural, rest)) },
          column-gutter: 8pt, align: horizon,
          ..(ic, ell(b.label, font: COND, size: fl, weight: 600), val).filter(x => x != none)))
      })
    }
  }))
}

#let text-block(b, cw) = {
  let (fs, lh) = if b.size == "s" { (19, 24) } else if b.size == "l" { (44, 52) } else { (26, 32) }
  pad(x: 16pt, top: 4pt, for l in b.lines {
    line(lh, ell(if l == "" { " " } else { l }, font: COND, size: pt(fs), weight: if b.bold { 800 } else { 500 },
      fill: if b.red { RED } else { black }), x: if b.align == "center" { center } else { left })
  })
}

// Šablona (Jinja + HTML) – v Typstu jen její text bez značek
#let template-block(b) = pad(x: 16pt, top: 4pt, {
  if has(b.error) { text(size: 15pt, weight: 700, fill: RED, txt(b.error)) }
  else { text(size: 20pt, data.template_text.at(b.id, default: "")) }
})

// ── Rozvržení: bloky na mřížce, čára nahoře a vlevo ─────────────────
#for b in view.blocks {
  let (lt, ll) = (b.line_top, b.line_left)
  let padx = (header: 32, departures: 32, agenda: 32, weather_now: 24, value: 24, text: 32, template: 32, footer: 32).at(b.type, default: 0)
  let pady = (header: 8, departures: 8, agenda: 6, text: 8, template: 8).at(b.type, default: 0)
  let cw = b.width - ll - padx
  let ch = b.height - lt - pady
  place(top + left, dx: pt(b.left), dy: pt(b.top), box(width: pt(b.width), height: pt(b.height), clip: true, {
    if lt > 0 { place(top + left, box(width: 100%, height: pt(lt), fill: black)) }
    if ll > 0 { place(top + left, box(width: pt(ll), height: 100%, fill: black)) }
    place(top + left, dx: pt(ll), dy: pt(lt), box(width: pt(b.width - ll), height: pt(b.height - lt), clip: true, {
      if b.type == "header" { header(b, cw) }
      else if b.type == "weather_now" { weather-now(b, cw) }
      else if b.type == "departures" { departures(b, cw) }
      else if b.type == "agenda" { agenda(b, cw) }
      else if b.type == "forecast" { forecast(b, cw) }
      else if b.type == "footer" { footer(b, cw) }
      else if b.type == "value" { value(b, cw, ch) }
      else if b.type == "text" { text-block(b, cw) }
      else if b.type == "template" { template-block(b) }
      else if b.type == "menu" { menu(b, cw, ch) }
      else if b.type == "bedtime" { bedtime(b, cw, ch) }
    }))
  }))
}
