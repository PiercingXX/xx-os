/*
 * Copyright (C) 2026 The XX-Phone Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * xx-keyboard theme bridge: read ~/.config/xx-wm/config.json (the same
 * file and preset keys the shell and common/xx_ui/theme.py use) and
 * render a GTK3 CSS override. A missing or invalid config falls back to
 * the amoled preset and never warns fatally. Preset colours come from
 * the generated xx-theme-presets.h (see tools/gen-xx-theme.py).
 */

#pragma once

#include <gio/gio.h>

G_BEGIN_DECLS

typedef struct {
  char     *key;
  char     *background;
  char     *surface;
  char     *surface_alt;
  char     *border;
  char     *foreground;
  char     *muted;
  char     *accent;
  char     *font_family;
  gboolean  dark;
} PosXxTheme;

char         *pos_xx_theme_config_path (void);
void          pos_xx_theme_load_from_path (PosXxTheme *theme, const char *path);
void          pos_xx_theme_load (PosXxTheme *theme);
void          pos_xx_theme_free (PosXxTheme *theme);
char         *pos_xx_theme_render_css (const PosXxTheme *theme);
/* Input-source id from the xx-wm keyboard_layout key, e.g. "us".
 * Always set; falls back to "us" on missing/garbage config. */
char         *pos_xx_theme_keyboard_layout_id (void);
char         *pos_xx_theme_keyboard_layout_id_from_path (const char *path);
#define XX_LAYOUT_QWERTY_ID "us"
#define XX_LAYOUT_PIERCING_ID "piercing-num"
/* Switcher order: primary first, then QWERTY and Piercing Num,
 * deduplicated. Takes ownership of nothing; returns a %NULL-terminated
 * array the caller frees with g_strfreev(). */
char        **pos_xx_theme_layout_order (const char *primary);
char        **pos_xx_theme_layout_order_from_path (const char *path);
/* Watch the config file for live theme reloads. Falls back to watching
 * the config directory when the file does not exist yet. The callback
 * gets the GFileMonitor "changed" signature. May return NULL. */
GFileMonitor *pos_xx_theme_watch (GCallback callback, gpointer user_data);

G_END_DECLS
