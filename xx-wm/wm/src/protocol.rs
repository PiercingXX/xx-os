//! Client bindings generated from river's protocol files.
#![allow(
    dead_code,
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    unused_imports,
    unused_variables,
    missing_docs,
    clippy::all
)]

pub mod wm {
    use wayland_client;
    use wayland_client::protocol::*;

    pub mod __interfaces {
        use wayland_client::protocol::__interfaces::*;
        wayland_scanner::generate_interfaces!("protocols/river-window-management-v1.xml");
    }
    use self::__interfaces::*;

    wayland_scanner::generate_client_code!("protocols/river-window-management-v1.xml");
}

pub mod xkb {
    use super::wm::*;
    use wayland_client;
    use wayland_client::protocol::*;

    pub mod __interfaces {
        use super::super::wm::__interfaces::*;
        use wayland_client::protocol::__interfaces::*;
        wayland_scanner::generate_interfaces!("protocols/river-xkb-bindings-v1.xml");
    }
    use self::__interfaces::*;

    wayland_scanner::generate_client_code!("protocols/river-xkb-bindings-v1.xml");
}

pub mod layer {
    use super::wm::*;
    use wayland_client;
    use wayland_client::protocol::*;

    pub mod __interfaces {
        use super::super::wm::__interfaces::*;
        use wayland_client::protocol::__interfaces::*;
        wayland_scanner::generate_interfaces!("protocols/river-layer-shell-v1.xml");
    }
    use self::__interfaces::*;

    wayland_scanner::generate_client_code!("protocols/river-layer-shell-v1.xml");
}
