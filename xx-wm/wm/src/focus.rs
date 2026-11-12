//! Keyboard focus for one seat, decided once per manage sequence. Pure, so
//! it is unit-tested; wm.rs turns the answer into river requests.

use std::time::Duration;

/// Which layer surface (the XX-WM shell, the on-screen keyboard, a
/// launcher) river says holds keyboard focus.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LayerFocus {
    None,
    /// The layer surface keeps focus; river ignores focus requests.
    Exclusive,
    /// The layer surface has focus until the window manager moves it.
    NonExclusive,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Cmd<T> {
    Keep,
    Focus(T),
    Clear,
}

/// How long after a window first shows that its focus is sent again.
///
/// river 0.5 sends `zwp_text_input_v3.enter` only when keyboard focus
/// changes. A client that creates its text input after its window was
/// focused (GTK4 does so on its first frame, after river has already
/// focused the new window) never gets one, so tapping a text field never
/// activates the input method and the on-screen keyboard stays down.
/// Taking focus off the window for one manage sequence and giving it back
/// makes river enter every text input the client has by then. It is done
/// once per window: moving focus closes the window's popups.
pub const REFOCUS_DELAY: Duration = Duration::from_millis(300);

/// `sent` is the focus last requested from river (`None`: nothing yet, or
/// the record is stale and must be sent again), `want` the window that
/// should have keyboard focus, `refocus` whether `want` is due for its
/// one-time refocus (see [`REFOCUS_DELAY`]).
pub fn decide<T: PartialEq + Clone>(
    layer: LayerFocus,
    sent: &Option<Option<T>>,
    want: &Option<T>,
    refocus: bool,
) -> Cmd<T> {
    if layer == LayerFocus::Exclusive {
        return Cmd::Keep;
    }
    if sent.as_ref() == Some(want) {
        // A layer surface with non-exclusive focus keeps it until the
        // wanted window changes or the window is interacted with.
        return if refocus && want.is_some() && layer == LayerFocus::None { Cmd::Clear } else { Cmd::Keep };
    }
    match want {
        Some(w) => Cmd::Focus(w.clone()),
        None => Cmd::Clear,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use LayerFocus as L;

    #[test]
    fn first_focus_is_sent() {
        assert_eq!(decide(L::None, &None, &Some(1), false), Cmd::Focus(1));
        assert_eq!(decide::<u32>(L::None, &None, &None, false), Cmd::Clear);
    }

    #[test]
    fn unchanged_focus_is_not_sent_again() {
        assert_eq!(decide(L::None, &Some(Some(1)), &Some(1), false), Cmd::Keep);
        assert_eq!(decide::<u32>(L::None, &Some(None), &None, false), Cmd::Keep);
    }

    #[test]
    fn a_new_window_takes_focus() {
        assert_eq!(decide(L::None, &Some(Some(1)), &Some(2), false), Cmd::Focus(2));
    }

    #[test]
    fn exclusive_layer_focus_is_left_alone() {
        assert_eq!(decide(L::Exclusive, &Some(Some(1)), &Some(2), false), Cmd::Keep);
        assert_eq!(decide(L::Exclusive, &None, &Some(1), true), Cmd::Keep);
    }

    #[test]
    fn non_exclusive_layer_focus_is_not_stolen_back() {
        // the window we focused is still the one we want: the layer
        // surface the user tapped keeps the keyboard
        assert_eq!(decide(L::NonExclusive, &Some(Some(1)), &Some(1), false), Cmd::Keep);
        assert_eq!(decide(L::NonExclusive, &Some(Some(1)), &Some(1), true), Cmd::Keep);
        // a different window wants focus (opened, switched to): it gets it
        assert_eq!(decide(L::NonExclusive, &Some(Some(1)), &Some(2), false), Cmd::Focus(2));
        // a tap on the window resets the record, which hands focus back
        assert_eq!(decide(L::NonExclusive, &None, &Some(1), false), Cmd::Focus(1));
    }

    #[test]
    fn refocus_clears_then_focuses_again() {
        let mut sent = Some(Some(7));
        let first = decide(L::None, &sent, &Some(7), true);
        assert_eq!(first, Cmd::Clear);
        sent = Some(None);
        // next manage sequence, refocus no longer due
        assert_eq!(decide(L::None, &sent, &Some(7), false), Cmd::Focus(7));
    }

    #[test]
    fn refocus_of_a_window_being_focused_now_is_just_the_focus() {
        assert_eq!(decide(L::None, &Some(Some(1)), &Some(7), true), Cmd::Focus(7));
        assert_eq!(decide(L::None, &None, &Some(7), true), Cmd::Focus(7));
    }

    #[test]
    fn refocus_with_nothing_focused_does_nothing() {
        assert_eq!(decide::<u32>(L::None, &Some(None), &None, true), Cmd::Keep);
    }
}
