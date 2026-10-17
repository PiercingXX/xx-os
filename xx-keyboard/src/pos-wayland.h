/*
 * Copyright (C) 2025 Phosh.mobi e.V.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Author: Guido Günther <agx@sigxcpu.org>
 */
#pragma once

#include "pos-config.h"
#include "pos.h"

#include "ext-data-control-v1-client-protocol.h"
#include "input-method-unstable-v2-client-protocol.h"
#include "phoc-device-state-unstable-v1-client-protocol.h"
#include "virtual-keyboard-unstable-v1-client-protocol.h"
#include "wlr-data-control-unstable-v1-client-protocol.h"
#include "wlr-foreign-toplevel-management-unstable-v1-client-protocol.h"
#include "xdg-output-unstable-v1-client-protocol.h"

#include <glib-object.h>

G_BEGIN_DECLS

#define POS_TYPE_WAYLAND pos_wayland_get_type()

G_DECLARE_FINAL_TYPE (PosWayland, pos_wayland, POS, WAYLAND, GObject)

/**
 * PosDataControlVariant:
 * @POS_DATA_CONTROL_NONE: No data-control protocol offered, paste stays disabled
 * @POS_DATA_CONTROL_EXT: `ext_data_control_manager_v1` (preferred, the successor)
 * @POS_DATA_CONTROL_WLR: `zwlr_data_control_manager_v1` (older compositors,
 *   e.g. phoc 0.44 on the FuriPhone FLX1). The wire messages we use
 *   (get_data_device, data_offer/selection/finished, offer/receive)
 *   are identical to the ext variant.
 */
typedef enum {
  POS_DATA_CONTROL_NONE = 0,
  POS_DATA_CONTROL_EXT,
  POS_DATA_CONTROL_WLR,
} PosDataControlVariant;

PosDataControlVariant               pos_data_control_pick (gboolean has_ext,
                                                           gboolean has_wlr);
gboolean                            pos_wayland_have_required (gboolean has_input_method,
                                                               gboolean has_virtual_keyboard,
                                                               gboolean has_seat,
                                                               gboolean has_layer_shell);

PosWayland *                          pos_wayland_get_default (void);
gboolean                              pos_wayland_has_wl_protcols (PosWayland *self);
GPtrArray *                           pos_wayland_get_outputs (PosWayland *self);
guint                                 pos_wayland_get_n_outputs (PosWayland *self);

struct wl_display *                      pos_wayland_get_wl_display (PosWayland *self);
struct zwp_input_method_manager_v2 *     pos_wayland_get_zwp_input_method_manager_v2 (PosWayland *self);
struct zwp_virtual_keyboard_manager_v1 * pos_wayland_get_zwp_virtual_keyboard_manager_v1 (PosWayland *self);
struct wl_seat *                         pos_wayland_get_wl_seat (PosWayland *self);
struct zwlr_foreign_toplevel_manager_v1* pos_wayland_get_zwlr_foreign_toplevel_manager_v1 (PosWayland *self);
struct zwlr_layer_shell_v1 *             pos_wayland_get_zwlr_layer_shell_v1 (PosWayland *self);
struct zxdg_output_manager_v1 *          pos_wayland_get_zxdg_output_manager_v1 (PosWayland *self);
struct zphoc_device_state_v1 *           pos_wayland_get_zphoc_device_state_v1 (PosWayland *self);
struct ext_data_control_manager_v1 *     pos_wayland_get_ext_data_control_manager_v1 (PosWayland *self);
struct zwlr_data_control_manager_v1 *  pos_wayland_get_zwlr_data_control_manager_v1 (PosWayland *self);

G_END_DECLS
