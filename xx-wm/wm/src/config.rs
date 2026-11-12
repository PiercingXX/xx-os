//! Configuration: a small line format whose defaults are the piercing-dots
//! Hyprland binds, so the docked desktop behaves like the owner's desktop.
//!
//! ```text
//! # comment
//! set gaps_in 8
//! bind Super+W spawn kitty
//! bind Super+Shift+H move left
//! unbind Super+D
//! ```

use crate::keysym;
use crate::layout::Dir;

#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    Spawn(String),
    Close,
    Focus(Dir),
    Move(Dir),
    /// Left/Up shrink the focused window's width/height, Right/Down grow it.
    Resize(Dir),
    Workspace(usize),
    /// Move the focused window to a workspace and follow it.
    Send(usize),
    /// Move the focused window to a workspace and stay put.
    SendSilent(usize),
    ToggleFloat,
    ToggleFullscreen,
    ToggleSplit,
    Launcher,
    /// Hand a verb to the XX-WM shell (`bar`, `keybinds`): it reaches the
    /// shell over the control socket's `subscribe state` stream.
    Shell(String),
}

impl Action {
    /// The wm.conf words for this action; `parse_action` reads them back.
    pub fn describe(&self) -> String {
        match self {
            Action::Spawn(cmd) => format!("spawn {cmd}"),
            Action::Close => "close".into(),
            Action::Focus(d) => format!("focus {}", d.name()),
            Action::Move(d) => format!("move {}", d.name()),
            Action::Resize(d) => format!("resize {}", d.name()),
            Action::Workspace(n) => format!("workspace {n}"),
            Action::Send(n) => format!("send {n}"),
            Action::SendSilent(n) => format!("send-silent {n}"),
            Action::ToggleFloat => "float".into(),
            Action::ToggleFullscreen => "fullscreen".into(),
            Action::ToggleSplit => "toggle-split".into(),
            Action::Launcher => "launcher".into(),
            Action::Shell(verb) => format!("shell {verb}"),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Bind {
    pub mods: u32,
    pub keysym: u32,
    pub combo: String,
    pub action: Action,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HandsetRule {
    /// Portrait outputs are handsets, landscape outputs are desktops.
    Auto,
    Always,
    Never,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub binds: Vec<Bind>,
    pub gaps_in: i32,
    pub gaps_out: i32,
    pub border: i32,
    pub border_active: u32,
    pub border_inactive: u32,
    pub handset: HandsetRule,
    pub launcher: String,
    /// Tapping Super on its own runs the launcher.
    pub super_tap: bool,
    pub resize_step: f32,
}

/// The owner's binds from piercing-dots (`dots/hypr/hyprland/keybinds.lua`).
/// Session-ending binds (shutdown, poweroff, suspend) are deliberately not
/// defaults: a window manager running nested must never power off the host.
pub const DEFAULTS: &str = r#"
set gaps_in 8
set gaps_out 10
set border 2
set border_active ffffff
set border_inactive 414141
set handset auto
set super_tap on
set resize_step 0.05
set launcher [ -x ~/.scripts/Control-Scripts/wm-app-launcher.sh ] && exec bash ~/.scripts/Control-Scripts/wm-app-launcher.sh || exec fuzzel

bind Super+Q close
bind Super+Shift+Q close
bind Alt+Tab launcher
bind Super+R shell bar
bind Super+slash shell keybinds
bind Super+Shift+F fullscreen
bind Super+F float
bind Super+X toggle-split

bind Super+W spawn kitty
bind Super+T spawn kitty tmux
bind Super+Z spawn kitty yazi
bind Super+Shift+Z spawn nautilus --new-window
bind Super+V spawn kitty nvim
bind Super+Alt+V spawn code
bind Super+Shift+V spawn opencode-desktop
bind Super+C spawn waterfox || flatpak run net.waterfox.waterfox
bind Super+B spawn obsidian || flatpak run md.obsidian.Obsidian
bind Super+D spawn discord || flatpak run com.discordapp.Discord
bind Super+G spawn gimp || flatpak run org.gimp.GIMP
bind Super+S spawn kitty ~/.scripts/PiercingXX-Settings-Menu/settings-menu.sh
bind Super+N spawn kitty ~/.scripts/Note-Scripts/open_daily_note.sh --split
bind Super+Shift+N spawn kitty ~/.scripts/Note-Scripts/open_daily_note.sh --view
bind Super+Alt+N spawn kitty ~/.scripts/Note-Scripts/open_daily_note.sh --todo
bind Super+I spawn kitty ~/.scripts/Note-Scripts/inventory.sh
bind Super+Shift+Y spawn bash ~/.scripts/Control-Scripts/wm-clipboard-menu.sh
bind Super+grave spawn xx-wm-ipc lock || loginctl lock-session
bind Super+P spawn bash ~/.scripts/Control-Scripts/notify-time.sh
bind Super+Shift+P spawn bash ~/.scripts/Control-Scripts/notify-calendar.sh
bind Super+O spawn bash ~/.scripts/PiercingXX-Settings-Menu/audio-input-manager.sh --toggle-output
bind Print spawn ~/.scripts/Control-Scripts/wm-screenshot.sh region
bind Shift+Print spawn ~/.scripts/Control-Scripts/wm-screenshot.sh window
bind Ctrl+Alt+Delete spawn kitty flatpak run io.missioncenter.MissionCenter

bind Super+H focus left
bind Super+J focus down
bind Super+K focus up
bind Super+L focus right
bind Super+Left focus left
bind Super+Down focus down
bind Super+Up focus up
bind Super+Right focus right
bind Super+Shift+H move left
bind Super+Shift+J move down
bind Super+Shift+K move up
bind Super+Shift+L move right
bind Super+Shift+Left move left
bind Super+Shift+Down move down
bind Super+Shift+Up move up
bind Super+Shift+Right move right
bind Super+Alt+H resize left
bind Super+Alt+J resize down
bind Super+Alt+K resize up
bind Super+Alt+L resize right
bind Super+Alt+Left resize left
bind Super+Alt+Down resize down
bind Super+Alt+Up resize up
bind Super+Alt+Right resize right

bind XF86AudioPlay spawn playerctl --all-players play-pause
bind XF86AudioNext spawn playerctl next
bind XF86AudioPrev spawn playerctl previous
bind XF86AudioRaiseVolume spawn ~/.scripts/Control-Scripts/volume.sh inc
bind XF86AudioLowerVolume spawn ~/.scripts/Control-Scripts/volume.sh dec
bind XF86AudioMute spawn ~/.scripts/Control-Scripts/volume.sh mute
bind XF86AudioMicMute spawn ~/.scripts/Control-Scripts/mic-toggle-notify.sh
"#;

impl Config {
    pub fn defaults() -> Config {
        let mut c = Config {
            binds: Vec::new(),
            gaps_in: 0,
            gaps_out: 0,
            border: 0,
            border_active: 0xffffff,
            border_inactive: 0x414141,
            handset: HandsetRule::Auto,
            launcher: "fuzzel".into(),
            super_tap: false,
            resize_step: 0.05,
        };
        for line in DEFAULTS.lines() {
            c.apply(line).expect("built-in defaults parse");
        }
        for n in 1..=10usize {
            let key = (n % 10).to_string();
            for (prefix, action) in [
                ("Super", Action::Workspace(n)),
                ("Super+Shift", Action::Send(n)),
                ("Super+Alt", Action::SendSilent(n)),
            ] {
                let combo = format!("{prefix}+{key}");
                let (m, k) = parse_combo(&combo).expect("workspace combo");
                c.binds.push(Bind { mods: m, keysym: k, combo, action });
            }
        }
        c
    }

    /// Defaults, then the user's file (if any). Lines that fail to parse are
    /// reported and skipped; one typo never loses the whole config.
    pub fn load(text: Option<&str>) -> (Config, Vec<String>) {
        let mut c = Config::defaults();
        let mut errors = Vec::new();
        if let Some(text) = text {
            for (i, line) in text.lines().enumerate() {
                if let Err(e) = c.apply(line) {
                    errors.push(format!("line {}: {e}", i + 1));
                }
            }
        }
        (c, errors)
    }

    pub fn apply(&mut self, line: &str) -> Result<(), String> {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            return Ok(());
        }
        let (word, rest) = split_word(line);
        match word {
            "set" => self.set(rest),
            "bind" => {
                let (combo, action_text) = split_word(rest);
                let (m, k) = parse_combo(combo).ok_or_else(|| format!("bad key combo {combo:?}"))?;
                let action = parse_action(action_text)?;
                self.binds.retain(|b| !(b.mods == m && b.keysym == k));
                self.binds.push(Bind { mods: m, keysym: k, combo: combo.to_string(), action });
                Ok(())
            }
            "unbind" => {
                let (m, k) = parse_combo(rest.trim()).ok_or_else(|| format!("bad key combo {rest:?}"))?;
                self.binds.retain(|b| !(b.mods == m && b.keysym == k));
                Ok(())
            }
            other => Err(format!("unknown command {other:?}")),
        }
    }

    fn set(&mut self, rest: &str) -> Result<(), String> {
        let (key, value) = split_word(rest);
        let value = value.trim();
        let int = || value.parse::<i32>().map_err(|_| format!("{key}: not a number: {value:?}"));
        match key {
            "gaps_in" => self.gaps_in = int()?.max(0),
            "gaps_out" => self.gaps_out = int()?.max(0),
            "border" => self.border = int()?.max(0),
            "border_active" => self.border_active = parse_rgb(value)?,
            "border_inactive" => self.border_inactive = parse_rgb(value)?,
            "launcher" => self.launcher = value.to_string(),
            "resize_step" => {
                let v = value.parse::<f32>().map_err(|_| format!("resize_step: {value:?}"))?;
                self.resize_step = v.clamp(0.01, 0.5);
            }
            "super_tap" => self.super_tap = parse_bool(value)?,
            "handset" => {
                self.handset = match value {
                    "auto" => HandsetRule::Auto,
                    "always" => HandsetRule::Always,
                    "never" => HandsetRule::Never,
                    _ => return Err(format!("handset: auto, always or never, not {value:?}")),
                }
            }
            other => return Err(format!("unknown setting {other:?}")),
        }
        Ok(())
    }
}

fn split_word(s: &str) -> (&str, &str) {
    let s = s.trim_start();
    match s.find(char::is_whitespace) {
        Some(i) => (&s[..i], s[i..].trim_start()),
        None => (s, ""),
    }
}

fn parse_bool(v: &str) -> Result<bool, String> {
    match v {
        "on" | "true" | "yes" | "1" => Ok(true),
        "off" | "false" | "no" | "0" => Ok(false),
        _ => Err(format!("expected on/off, got {v:?}")),
    }
}

fn parse_rgb(v: &str) -> Result<u32, String> {
    let hex = v.trim_start_matches('#');
    if hex.len() != 6 {
        return Err(format!("colour must be rrggbb, got {v:?}"));
    }
    u32::from_str_radix(hex, 16).map_err(|_| format!("bad colour {v:?}"))
}

/// `Super+Shift+H` → (modifier bits, keysym). The last part is the key.
pub fn parse_combo(combo: &str) -> Option<(u32, u32)> {
    let parts: Vec<&str> = combo.split('+').filter(|p| !p.is_empty()).collect();
    let (key, modifiers) = parts.split_last()?;
    let mut m = 0;
    for name in modifiers {
        m |= keysym::modifier_from_name(name)?;
    }
    Some((m, keysym::from_name(key)?))
}

fn parse_dir(s: &str) -> Result<Dir, String> {
    match s.trim() {
        "left" => Ok(Dir::Left),
        "right" => Ok(Dir::Right),
        "up" => Ok(Dir::Up),
        "down" => Ok(Dir::Down),
        other => Err(format!("direction must be left/right/up/down, not {other:?}")),
    }
}

fn parse_workspace(s: &str) -> Result<usize, String> {
    match s.trim().parse::<usize>() {
        Ok(n) if (1..=10).contains(&n) => Ok(n),
        _ => Err(format!("workspace must be 1-10, not {s:?}")),
    }
}

/// Shell verbs travel as one JSON string on the state stream; keep them
/// to plain words so nothing needs escaping or can smuggle a second line.
fn is_shell_verb(s: &str) -> bool {
    !s.is_empty() && s.len() <= 32 && s.bytes().all(|b| b.is_ascii_lowercase() || b == b'-' || b == b'.')
}

pub fn parse_action(text: &str) -> Result<Action, String> {
    let (verb, arg) = split_word(text);
    Ok(match verb {
        "spawn" if !arg.is_empty() => Action::Spawn(arg.to_string()),
        "close" => Action::Close,
        "focus" => Action::Focus(parse_dir(arg)?),
        "move" => Action::Move(parse_dir(arg)?),
        "resize" => Action::Resize(parse_dir(arg)?),
        "workspace" => Action::Workspace(parse_workspace(arg)?),
        "send" => Action::Send(parse_workspace(arg)?),
        "send-silent" => Action::SendSilent(parse_workspace(arg)?),
        "float" => Action::ToggleFloat,
        "fullscreen" => Action::ToggleFullscreen,
        "toggle-split" => Action::ToggleSplit,
        "launcher" => Action::Launcher,
        "shell" if is_shell_verb(arg) => Action::Shell(arg.to_string()),
        "shell" => return Err(format!("shell verb must be one lowercase word, not {arg:?}")),
        other => return Err(format!("unknown action {other:?}")),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn find(c: &Config, combo: &str) -> Option<Action> {
        let (m, k) = parse_combo(combo).unwrap();
        c.binds.iter().find(|b| b.mods == m && b.keysym == k).map(|b| b.action.clone())
    }

    #[test]
    fn defaults_are_the_piercing_dots_binds() {
        let c = Config::defaults();
        assert_eq!(find(&c, "Super+Q"), Some(Action::Close));
        assert_eq!(find(&c, "Super+W"), Some(Action::Spawn("kitty".into())));
        assert_eq!(find(&c, "Super+H"), Some(Action::Focus(Dir::Left)));
        assert_eq!(find(&c, "Super+Shift+L"), Some(Action::Move(Dir::Right)));
        assert_eq!(find(&c, "Super+Alt+J"), Some(Action::Resize(Dir::Down)));
        assert_eq!(find(&c, "Super+1"), Some(Action::Workspace(1)));
        assert_eq!(find(&c, "Super+0"), Some(Action::Workspace(10)));
        assert_eq!(find(&c, "Super+Shift+3"), Some(Action::Send(3)));
        assert_eq!(find(&c, "Super+Alt+3"), Some(Action::SendSilent(3)));
        assert_eq!(find(&c, "Super+X"), Some(Action::ToggleSplit));
        assert_eq!(c.gaps_in, 8);
        assert_eq!(c.gaps_out, 10);
        assert!(c.super_tap);
    }

    #[test]
    fn defaults_never_end_the_session() {
        let c = Config::defaults();
        for b in &c.binds {
            if let Action::Spawn(cmd) = &b.action {
                for bad in ["shutdown", "poweroff", "suspend", "reboot", "exit_session"] {
                    assert!(!cmd.contains(bad), "{} runs {cmd}", b.combo);
                }
            }
        }
    }

    #[test]
    fn no_two_default_binds_share_a_combo() {
        let c = Config::defaults();
        for (i, a) in c.binds.iter().enumerate() {
            for b in &c.binds[i + 1..] {
                assert!(!(a.mods == b.mods && a.keysym == b.keysym), "{} twice", a.combo);
            }
        }
    }

    #[test]
    fn user_file_overrides_and_unbinds() {
        let (c, errors) = Config::load(Some(
            "bind Super+W spawn foot\nunbind Super+D\nset gaps_in 4\nbogus line\nbind Super+Nope close\n",
        ));
        assert_eq!(find(&c, "Super+W"), Some(Action::Spawn("foot".into())));
        assert_eq!(find(&c, "Super+D"), None);
        assert_eq!(c.gaps_in, 4);
        assert_eq!(errors.len(), 2, "{errors:?}");
    }

    #[test]
    fn spawn_keeps_the_whole_command() {
        assert_eq!(
            parse_action("spawn waterfox || flatpak run net.waterfox.waterfox"),
            Ok(Action::Spawn("waterfox || flatpak run net.waterfox.waterfox".into()))
        );
        assert!(parse_action("spawn").is_err());
        assert!(parse_action("workspace 11").is_err());
    }

    #[test]
    fn describe_reads_back_for_every_default() {
        let c = Config::defaults();
        for b in &c.binds {
            assert_eq!(parse_action(&b.action.describe()), Ok(b.action.clone()), "{}", b.combo);
        }
    }

    #[test]
    fn shell_verbs() {
        let c = Config::defaults();
        assert_eq!(find(&c, "Super+R"), Some(Action::Shell("bar".into())));
        assert_eq!(find(&c, "Super+slash"), Some(Action::Shell("keybinds".into())));
        assert_eq!(parse_action("shell launcher"), Ok(Action::Shell("launcher".into())));
        assert!(parse_action("shell").is_err());
        assert!(parse_action("shell two words").is_err());
        assert!(parse_action("shell Bar").is_err());
        assert!(parse_action("shell a\"b").is_err());
    }

    #[test]
    fn combos() {
        assert_eq!(parse_combo("Super+Shift+H"), Some((65, 0x68)));
        assert_eq!(parse_combo("Print"), Some((0, 0xff61)));
        assert_eq!(parse_combo("Hyper+H"), None);
    }
}
