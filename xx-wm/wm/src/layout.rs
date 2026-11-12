//! Pure layout maths: dwindle tiling, directional neighbours, resizing.
//! Nothing here talks to the compositor, so all of it is unit-tested.

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl Rect {
    pub const fn new(x: i32, y: i32, w: i32, h: i32) -> Self {
        Rect { x, y, w, h }
    }

    /// The rect with `n` pixels taken off every side (never below 1x1).
    pub fn shrink(self, n: i32) -> Rect {
        Rect {
            x: self.x + n,
            y: self.y + n,
            w: (self.w - 2 * n).max(1),
            h: (self.h - 2 * n).max(1),
        }
    }

    pub fn center(self) -> (i32, i32) {
        (self.x + self.w / 2, self.y + self.h / 2)
    }

    /// The overlap of two rects, if they overlap at all.
    pub fn intersect(self, o: Rect) -> Option<Rect> {
        let x = self.x.max(o.x);
        let y = self.y.max(o.y);
        let w = (self.x + self.w).min(o.x + o.w) - x;
        let h = (self.y + self.h).min(o.y + o.h) - y;
        (w > 0 && h > 0).then(|| Rect::new(x, y, w, h))
    }
}

/// The part of an output windows may use: what river reports as left after
/// the layer surfaces' exclusive zones (the shell's status bar, the
/// on-screen keyboard), in global coordinates. An area river has not sent
/// yet, or one that does not lie on the output (stale after the output
/// moved, or zones that eat the whole screen), falls back to the output.
pub fn usable_area(output: Rect, non_exclusive: Option<Rect>) -> Rect {
    non_exclusive.and_then(|a| a.intersect(output)).unwrap_or(output)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Dir {
    Left,
    Down,
    Up,
    Right,
}

impl Dir {
    pub fn horizontal(self) -> bool {
        matches!(self, Dir::Left | Dir::Right)
    }
}

/// How a level of the dwindle tree divides its area.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Axis {
    /// Side by side: the first part is on the left.
    Columns,
    /// Stacked: the first part is on top.
    Rows,
}

/// Per-level settings a user can change with resize / toggle-split.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Split {
    pub ratio: f32,
    pub flip: bool,
}

impl Default for Split {
    fn default() -> Self {
        Split { ratio: 0.5, flip: false }
    }
}

pub const MIN_RATIO: f32 = 0.1;
pub const MAX_RATIO: f32 = 0.9;

/// The result of laying out `n` windows: one rect per window, and the axis
/// each split level used (level `i` separates window `i` from windows
/// `i+1..n`).
#[derive(Clone, Debug, PartialEq)]
pub struct Tiling {
    pub rects: Vec<Rect>,
    pub axes: Vec<Axis>,
}

/// Dwindle tiling as in Hyprland/bspwm: window `i` takes the first part of
/// whatever area windows `0..i` left over, split along the longer side
/// (a level's `flip` swaps that). `gap` is the space between siblings.
pub fn dwindle(n: usize, area: Rect, gap: i32, splits: &[Split]) -> Tiling {
    let mut rects = Vec::with_capacity(n);
    let mut axes = Vec::with_capacity(n.saturating_sub(1));
    let mut rest = area;
    for i in 0..n {
        if i + 1 == n {
            rects.push(rest);
            break;
        }
        let split = splits.get(i).copied().unwrap_or_default();
        let ratio = split.ratio.clamp(MIN_RATIO, MAX_RATIO);
        let wide = rest.w >= rest.h;
        let axis = if wide != split.flip { Axis::Columns } else { Axis::Rows };
        axes.push(axis);
        match axis {
            Axis::Columns => {
                let total = (rest.w - gap).max(2);
                let first = ((total as f32) * ratio).round() as i32;
                let first = first.clamp(1, total - 1);
                rects.push(Rect::new(rest.x, rest.y, first, rest.h));
                rest = Rect::new(rest.x + first + gap, rest.y, total - first, rest.h);
            }
            Axis::Rows => {
                let total = (rest.h - gap).max(2);
                let first = ((total as f32) * ratio).round() as i32;
                let first = first.clamp(1, total - 1);
                rects.push(Rect::new(rest.x, rest.y, rest.w, first));
                rest = Rect::new(rest.x, rest.y + first + gap, rest.w, total - first);
            }
        }
    }
    Tiling { rects, axes }
}

/// The rect nearest to `rects[from]` in direction `dir`, preferring rects
/// that overlap it on the other axis (a true neighbour) over diagonal ones.
pub fn neighbor(rects: &[Rect], from: usize, dir: Dir) -> Option<usize> {
    let f = *rects.get(from)?;
    let (fcx, fcy) = f.center();
    let mut best: Option<(i64, usize)> = None;
    for (i, &c) in rects.iter().enumerate() {
        if i == from {
            continue;
        }
        let (ccx, ccy) = c.center();
        let (ahead, gap, overlap, drift) = match dir {
            Dir::Left => (ccx < fcx, f.x - (c.x + c.w), overlaps(f.y, f.h, c.y, c.h), (ccy - fcy).abs()),
            Dir::Right => (ccx > fcx, c.x - (f.x + f.w), overlaps(f.y, f.h, c.y, c.h), (ccy - fcy).abs()),
            Dir::Up => (ccy < fcy, f.y - (c.y + c.h), overlaps(f.x, f.w, c.x, c.w), (ccx - fcx).abs()),
            Dir::Down => (ccy > fcy, c.y - (f.y + f.h), overlaps(f.x, f.w, c.x, c.w), (ccx - fcx).abs()),
        };
        if !ahead {
            continue;
        }
        let score = (if overlap { 0 } else { 1_000_000_000i64 })
            + (gap.max(0) as i64) * 1000
            + drift as i64;
        if best.map_or(true, |(s, _)| score < s) {
            best = Some((score, i));
        }
    }
    best.map(|(_, i)| i)
}

fn overlaps(a: i32, alen: i32, b: i32, blen: i32) -> bool {
    a < b + blen && b < a + alen
}

/// Grow (`grow == true`) or shrink window `k`'s width (`horizontal`) or
/// height by `step` of the split that bounds it on that axis. Returns
/// whether a split changed. Window `k` is the first part of level `k` and
/// lies inside the second part of every level before it.
pub fn resize(
    splits: &mut Vec<Split>,
    axes: &[Axis],
    k: usize,
    horizontal: bool,
    grow: bool,
    step: f32,
) -> bool {
    let want = if horizontal { Axis::Columns } else { Axis::Rows };
    if axes.is_empty() {
        return false;
    }
    let top = k.min(axes.len() - 1);
    for level in (0..=top).rev() {
        if axes[level] != want {
            continue;
        }
        if splits.len() <= level {
            splits.resize(level + 1, Split::default());
        }
        let first_part = level == k;
        let delta = if grow == first_part { step } else { -step };
        let s = &mut splits[level];
        let before = s.ratio;
        s.ratio = (s.ratio + delta).clamp(MIN_RATIO, MAX_RATIO);
        return (s.ratio - before).abs() > f32::EPSILON;
    }
    false
}

/// Flip the axis of the level that splits window `k` off (or, for the last
/// window, the level above it).
pub fn toggle_split(splits: &mut Vec<Split>, n: usize, k: usize) -> bool {
    if n < 2 {
        return false;
    }
    let level = k.min(n - 2);
    if splits.len() <= level {
        splits.resize(level + 1, Split::default());
    }
    splits[level].flip = !splits[level].flip;
    true
}

/// The rect a floating window of size `w`x`h` gets when first shown:
/// centred in `area`, never larger than it.
pub fn centered(area: Rect, w: i32, h: i32) -> Rect {
    let w = w.clamp(1, area.w.max(1));
    let h = h.clamp(1, area.h.max(1));
    Rect::new(area.x + (area.w - w) / 2, area.y + (area.h - h) / 2, w, h)
}

#[cfg(test)]
mod tests {
    use super::*;

    const AREA: Rect = Rect::new(0, 0, 1000, 600);

    fn even(n: usize) -> Vec<Split> {
        vec![Split::default(); n]
    }

    #[test]
    fn one_window_fills_the_area() {
        let t = dwindle(1, AREA, 8, &[]);
        assert_eq!(t.rects, vec![AREA]);
        assert!(t.axes.is_empty());
    }

    #[test]
    fn two_windows_side_by_side_on_a_wide_area() {
        let t = dwindle(2, AREA, 10, &even(1));
        assert_eq!(t.axes, vec![Axis::Columns]);
        assert_eq!(t.rects[0], Rect::new(0, 0, 495, 600));
        assert_eq!(t.rects[1], Rect::new(505, 0, 495, 600));
    }

    #[test]
    fn three_windows_dwindle_into_the_right_column() {
        let t = dwindle(3, AREA, 0, &even(2));
        assert_eq!(t.axes, vec![Axis::Columns, Axis::Rows]);
        assert_eq!(t.rects[0], Rect::new(0, 0, 500, 600));
        assert_eq!(t.rects[1], Rect::new(500, 0, 500, 300));
        assert_eq!(t.rects[2], Rect::new(500, 300, 500, 300));
    }

    #[test]
    fn tall_area_stacks_first() {
        let t = dwindle(2, Rect::new(0, 0, 400, 900), 0, &even(1));
        assert_eq!(t.axes, vec![Axis::Rows]);
        assert_eq!(t.rects[0].h, 450);
    }

    #[test]
    fn flip_swaps_the_axis() {
        let t = dwindle(2, AREA, 0, &[Split { ratio: 0.5, flip: true }]);
        assert_eq!(t.axes, vec![Axis::Rows]);
    }

    #[test]
    fn rects_never_overlap_and_cover_the_area_without_gaps() {
        for n in 1..9 {
            let t = dwindle(n, AREA, 0, &even(n));
            let area: i64 = t.rects.iter().map(|r| r.w as i64 * r.h as i64).sum();
            assert_eq!(area, 1000 * 600, "n={n}");
            for (i, a) in t.rects.iter().enumerate() {
                for b in &t.rects[i + 1..] {
                    assert!(
                        !(overlaps(a.x, a.w, b.x, b.w) && overlaps(a.y, a.h, b.y, b.h)),
                        "n={n} {a:?} {b:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn neighbour_follows_the_layout() {
        let t = dwindle(3, AREA, 0, &even(2));
        assert_eq!(neighbor(&t.rects, 0, Dir::Right), Some(1));
        assert_eq!(neighbor(&t.rects, 1, Dir::Down), Some(2));
        assert_eq!(neighbor(&t.rects, 2, Dir::Up), Some(1));
        assert_eq!(neighbor(&t.rects, 2, Dir::Left), Some(0));
        assert_eq!(neighbor(&t.rects, 0, Dir::Left), None);
        assert_eq!(neighbor(&t.rects, 1, Dir::Up), None);
    }

    #[test]
    fn growing_the_first_window_widens_it() {
        let mut s = even(1);
        let t = dwindle(2, AREA, 0, &s);
        assert!(resize(&mut s, &t.axes, 0, true, true, 0.1));
        assert!(dwindle(2, AREA, 0, &s).rects[0].w > 500);
    }

    #[test]
    fn growing_the_second_window_moves_the_border_left() {
        let mut s = even(1);
        let t = dwindle(2, AREA, 0, &s);
        assert!(resize(&mut s, &t.axes, 1, true, true, 0.1));
        assert!(dwindle(2, AREA, 0, &s).rects[1].w > 500);
    }

    #[test]
    fn height_resize_uses_the_row_split() {
        let mut s = even(2);
        let t = dwindle(3, AREA, 0, &s);
        assert!(resize(&mut s, &t.axes, 1, false, true, 0.1));
        assert!(dwindle(3, AREA, 0, &s).rects[1].h > 300);
        // the column split was left alone
        assert_eq!(s[0].ratio, 0.5);
    }

    #[test]
    fn resize_with_no_matching_split_does_nothing() {
        let mut s = even(1);
        let t = dwindle(2, AREA, 0, &s);
        assert!(!resize(&mut s, &t.axes, 0, false, true, 0.1));
    }

    #[test]
    fn ratio_is_clamped() {
        let mut s = even(1);
        let t = dwindle(2, AREA, 0, &s);
        for _ in 0..20 {
            resize(&mut s, &t.axes, 0, true, true, 0.1);
        }
        assert!((s[0].ratio - MAX_RATIO).abs() < 1e-6);
    }

    #[test]
    fn toggle_split_flips_the_focused_level() {
        let mut s = Vec::new();
        assert!(toggle_split(&mut s, 3, 2));
        assert!(s[1].flip && !s[0].flip);
        assert!(!toggle_split(&mut s, 1, 0));
    }

    // A 450x900 handset with the shell's 28 px status bar on top and a
    // 200 px on-screen keyboard at the bottom, both exclusive zones.
    const PHONE: Rect = Rect::new(0, 0, 450, 900);

    #[test]
    fn usable_area_is_the_output_until_river_reports_one() {
        assert_eq!(usable_area(PHONE, None), PHONE);
    }

    #[test]
    fn handset_app_fills_what_bar_and_keyboard_leave() {
        let bar_only = Rect::new(0, 28, 450, 872);
        let bar_and_keyboard = Rect::new(0, 28, 450, 672);
        assert_eq!(usable_area(PHONE, Some(bar_only)), bar_only);
        assert_eq!(usable_area(PHONE, Some(bar_and_keyboard)), bar_and_keyboard);
        // the keyboard hides: the app grows back to the bar-only area
        assert_eq!(usable_area(PHONE, Some(bar_only)).h, 900 - 28);
    }

    #[test]
    fn usable_area_is_global_on_a_second_output() {
        let monitor = Rect::new(450, 0, 1920, 1080);
        let area = Rect::new(450, 32, 1920, 1048);
        assert_eq!(usable_area(monitor, Some(area)), area);
    }

    #[test]
    fn an_area_off_the_output_falls_back_to_the_output() {
        // stale: reported before the output moved to the right
        assert_eq!(usable_area(Rect::new(450, 0, 800, 600), Some(Rect::new(0, 0, 450, 900))), Rect::new(450, 0, 800, 600));
        // exclusive zones that eat the whole screen
        assert_eq!(usable_area(PHONE, Some(Rect::new(0, 28, 450, 0))), PHONE);
        assert_eq!(usable_area(PHONE, Some(Rect::new(0, 28, 450, -40))), PHONE);
    }

    #[test]
    fn an_area_hanging_off_the_output_is_clipped_to_it() {
        assert_eq!(usable_area(PHONE, Some(Rect::new(0, 28, 500, 1000))), Rect::new(0, 28, 450, 872));
    }

    #[test]
    fn desktop_tiles_stay_inside_the_usable_area() {
        let monitor = Rect::new(450, 0, 1000, 600);
        // a 40 px bar on top and a 200 px keyboard at the bottom
        let area = usable_area(monitor, Some(Rect::new(450, 40, 1000, 360)));
        let inner = area.shrink(8);
        for n in 1..6 {
            let t = dwindle(n, inner, 8, &even(n));
            let covered: i64 = t.rects.iter().map(|r| r.w as i64 * r.h as i64).sum();
            assert!(covered <= inner.w as i64 * inner.h as i64);
            for r in &t.rects {
                assert!(r.y >= 40 && r.y + r.h <= 400, "n={n} {r:?} crosses the bar or keyboard");
                assert!(r.x >= 450 && r.x + r.w <= 1450, "n={n} {r:?}");
            }
        }
        // without the keyboard the bottom row reaches further down
        let grown = usable_area(monitor, Some(Rect::new(450, 40, 1000, 560))).shrink(8);
        let a = dwindle(3, inner, 8, &even(2));
        let b = dwindle(3, grown, 8, &even(2));
        assert!(b.rects[2].y + b.rects[2].h > a.rects[2].y + a.rects[2].h);
    }

    #[test]
    fn intersect_of_disjoint_rects_is_none() {
        assert_eq!(Rect::new(0, 0, 10, 10).intersect(Rect::new(10, 0, 10, 10)), None);
        assert_eq!(Rect::new(0, 0, 10, 10).intersect(Rect::new(5, 5, 10, 10)), Some(Rect::new(5, 5, 5, 5)));
    }

    #[test]
    fn centered_fits_inside() {
        let r = centered(AREA, 2000, 100);
        assert_eq!(r, Rect::new(0, 250, 1000, 100));
    }
}
