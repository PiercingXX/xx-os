//! The xkbcommon keysyms the window manager binds, by name. A small table
//! instead of linking libxkbcommon: bindings only need names → numbers.

pub fn from_name(name: &str) -> Option<u32> {
    let lower = name.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    if bytes.len() == 1 {
        let c = bytes[0];
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            return Some(c as u32);
        }
    }
    if let Some(n) = lower.strip_prefix('f').and_then(|d| d.parse::<u32>().ok()) {
        if (1..=24).contains(&n) {
            return Some(0xffbe + n - 1);
        }
    }
    let sym = match lower.as_str() {
        "grave" | "`" => 0x0060,
        "slash" | "/" => 0x002f,
        "backslash" => 0x005c,
        "minus" | "-" => 0x002d,
        "equal" | "=" => 0x003d,
        "comma" | "," => 0x002c,
        "period" | "." => 0x002e,
        "semicolon" | ";" => 0x003b,
        "apostrophe" | "'" => 0x0027,
        "bracketleft" | "[" => 0x005b,
        "bracketright" | "]" => 0x005d,
        "space" => 0x0020,
        "return" | "enter" => 0xff0d,
        "tab" => 0xff09,
        "escape" | "esc" => 0xff1b,
        "backspace" => 0xff08,
        "delete" => 0xffff,
        "home" => 0xff50,
        "end" => 0xff57,
        "left" => 0xff51,
        "up" => 0xff52,
        "right" => 0xff53,
        "down" => 0xff54,
        "print" => 0xff61,
        "super_l" => 0xffeb,
        "super_r" => 0xffec,
        "xf86audioplay" => 0x1008ff14,
        "xf86audionext" => 0x1008ff17,
        "xf86audioprev" => 0x1008ff16,
        "xf86audioraisevolume" => 0x1008ff13,
        "xf86audiolowervolume" => 0x1008ff11,
        "xf86audiomute" => 0x1008ff12,
        "xf86audiomicmute" => 0x1008ffb2,
        "xf86monbrightnessup" => 0x1008ff02,
        "xf86monbrightnessdown" => 0x1008ff03,
        "xf86poweroff" => 0x1008ff2a,
        _ => return None,
    };
    Some(sym)
}

/// Modifier bits as river_seat_v1.modifiers defines them.
pub mod mods {
    pub const SHIFT: u32 = 1;
    pub const CTRL: u32 = 4;
    pub const ALT: u32 = 8;
    pub const MOD3: u32 = 32;
    pub const SUPER: u32 = 64;
    pub const MOD5: u32 = 128;
}

pub fn modifier_from_name(name: &str) -> Option<u32> {
    Some(match name.to_ascii_lowercase().as_str() {
        "super" | "mod4" | "logo" | "win" => mods::SUPER,
        "shift" => mods::SHIFT,
        "ctrl" | "control" => mods::CTRL,
        "alt" | "mod1" => mods::ALT,
        "mod3" => mods::MOD3,
        "mod5" | "altgr" => mods::MOD5,
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn letters_digits_and_names() {
        assert_eq!(from_name("H"), Some(0x68));
        assert_eq!(from_name("h"), Some(0x68));
        assert_eq!(from_name("0"), Some(0x30));
        assert_eq!(from_name("grave"), Some(0x60));
        assert_eq!(from_name("F1"), Some(0xffbe));
        assert_eq!(from_name("F12"), Some(0xffc9));
        assert_eq!(from_name("XF86AudioPlay"), Some(0x1008ff14));
        assert_eq!(from_name("nonsense"), None);
    }

    #[test]
    fn modifiers() {
        assert_eq!(modifier_from_name("Super"), Some(64));
        assert_eq!(modifier_from_name("ALT"), Some(8));
        assert_eq!(modifier_from_name("Hyper"), None);
    }
}
