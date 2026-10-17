/*
 * Copyright (C) 2026 The XX-Phone Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#define G_LOG_DOMAIN "pos-xx-theme"

#include "pos-xx-theme.h"
#include "xx-theme-presets.h"

#include <json-glib/json-glib.h>
#include <stdio.h>
#include <string.h>

#define XX_DEFAULT_LAYOUT_ID "us"


char *
pos_xx_theme_config_path (void)
{
  /* Mirrors xx_ui.theme.config_path: $XDG_CONFIG_HOME or ~/.config */
  return g_build_filename (g_get_user_config_dir (), "xx-wm", "config.json", NULL);
}


static const XxThemePreset *
find_preset (const char *key)
{
  for (int i = 0; i < XX_THEME_N_PRESETS; i++) {
    if (g_strcmp0 (xx_theme_presets[i].key, key) == 0)
      return &xx_theme_presets[i];
  }
  return NULL;
}


static const XxThemePreset *
default_preset (void)
{
  const XxThemePreset *preset = find_preset (xx_theme_default_preset);
  return preset != NULL ? preset : &xx_theme_presets[0];
}


static const char *
find_font_family (const char *key)
{
  const char *fallback = NULL;

  for (int i = 0; i < XX_THEME_N_FONTS; i++) {
    if (g_strcmp0 (xx_theme_fonts[i].key, key) == 0)
      return xx_theme_fonts[i].family;
    if (g_strcmp0 (xx_theme_fonts[i].key, xx_theme_default_font_key) == 0)
      fallback = xx_theme_fonts[i].family;
  }
  return fallback != NULL ? fallback : "Monospace";
}


static gboolean
parse_hex (const char *text, guint8 rgb[3])
{
  unsigned int r, g, b;

  if (text == NULL || strlen (text) != 7 || text[0] != '#')
    return FALSE;
  if (sscanf (text + 1, "%2x%2x%2x", &r, &g, &b) != 3)
    return FALSE;
  /* sscanf %x accepts signs and 0x prefixes; insist on plain hex */
  for (int i = 1; i < 7; i++) {
    if (!g_ascii_isxdigit (text[i]))
      return FALSE;
  }
  rgb[0] = (guint8) r;
  rgb[1] = (guint8) g;
  rgb[2] = (guint8) b;
  return TRUE;
}


static double
luminance (const guint8 rgb[3])
{
  return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0;
}


static void
fill_from_preset (PosXxTheme *theme, const XxThemePreset *preset)
{
  g_free (theme->key);
  g_free (theme->background);
  g_free (theme->surface);
  g_free (theme->surface_alt);
  g_free (theme->border);
  g_free (theme->foreground);
  g_free (theme->muted);
  g_free (theme->accent);

  theme->key = g_strdup (preset->key);
  theme->background = g_strdup (preset->background);
  theme->surface = g_strdup (preset->surface);
  theme->surface_alt = g_strdup (preset->surface_alt);
  theme->border = g_strdup (preset->border);
  theme->foreground = g_strdup (preset->foreground);
  theme->muted = g_strdup (preset->muted);
  theme->accent = g_strdup (preset->accent);
}


/* Mirror xx_ui.theme._derive_shades: scale factors with the near-black
 * special case, lowercase hex like Python's %02x. */
static void
derive_shades (const guint8 rgb[3], char **surface, char **surface_alt, char **border)
{
  static const double factors[3] = { 0.82, 0.72, 0.55 };
  char **outs[3] = { surface, surface_alt, border };
  guint8 max = MAX (rgb[0], MAX (rgb[1], rgb[2]));

  if (max <= 0x18) {
    *surface = g_strdup ("#111111");
    *surface_alt = g_strdup ("#181818");
    *border = g_strdup ("#2f2f2f");
    return;
  }
  for (int i = 0; i < 3; i++) {
    *outs[i] = g_strdup_printf ("#%02x%02x%02x",
                                (guint) (rgb[0] * factors[i]),
                                (guint) (rgb[1] * factors[i]),
                                (guint) (rgb[2] * factors[i]));
  }
}


static const char *
config_string (JsonObject *config, const char *member)
{
  JsonNode *node;

  if (config == NULL || !json_object_has_member (config, member))
    return NULL;
  node = json_object_get_member (config, member);
  if (!JSON_NODE_HOLDS_VALUE (node) || json_node_get_value_type (node) != G_TYPE_STRING)
    return NULL;
  return json_node_get_string (node);
}


void
pos_xx_theme_load_from_path (PosXxTheme *theme, const char *path)
{
  g_autoptr (JsonParser) parser = NULL;
  g_autoptr (GError) err = NULL;
  JsonObject *config = NULL;
  const XxThemePreset *preset;
  const char *key;
  guint8 rgb[3];

  memset (theme, 0, sizeof (*theme));
  preset = default_preset ();
  fill_from_preset (theme, preset);
  theme->font_family = g_strdup (find_font_family (xx_theme_default_font_key));
  theme->dark = TRUE;

  if (path == NULL)
    return;

  parser = json_parser_new ();
  if (!json_parser_load_from_file (parser, path, &err)) {
    g_debug ("xx theme: no usable config at %s (%s), using %s",
             path, err->message, preset->key);
    return;
  }

  if (!JSON_NODE_HOLDS_OBJECT (json_parser_get_root (parser))) {
    g_debug ("xx theme: config root is not an object, using %s", preset->key);
    return;
  }
  config = json_node_get_object (json_parser_get_root (parser));

  key = config_string (config, "theme");
  if (key == NULL)
    key = xx_theme_default_preset;

  if (g_strcmp0 (key, "custom") == 0) {
    const char *custom = config_string (config, "custom_background");
    if (parse_hex (custom, rgb)) {
      const XxThemePreset *text = luminance (rgb) < 0.5
        ? find_preset (xx_theme_default_preset) : find_preset ("paper");
      if (text == NULL)
        text = default_preset ();
      fill_from_preset (theme, text);
      g_free (theme->key);
      g_free (theme->background);
      theme->key = g_strdup ("custom");
      theme->background = g_strdup (custom);
      g_free (theme->surface);
      g_free (theme->surface_alt);
      g_free (theme->border);
      derive_shades (rgb, &theme->surface, &theme->surface_alt, &theme->border);
    } else {
      g_debug ("xx theme: bad custom_background, using %s", preset->key);
    }
  } else {
    const XxThemePreset *named = find_preset (key);
    if (named != NULL)
      fill_from_preset (theme, named);
    else
      g_debug ("xx theme: unknown preset '%s', using %s", key, preset->key);
  }

  if (parse_hex (theme->background, rgb))
    theme->dark = luminance (rgb) < 0.5;

  key = config_string (config, "font");
  if (key == NULL)
    key = xx_theme_default_font_key;
  if (g_strcmp0 (key, "custom") == 0) {
    const char *family = config_string (config, "custom_font_family");
    g_autofree char *copy = g_strdup (family != NULL ? family : "");
    g_free (theme->font_family);
    if (*g_strstrip (copy) != '\0')
      theme->font_family = g_steal_pointer (&copy);
    else
      theme->font_family = g_strdup (find_font_family (xx_theme_default_font_key));
  } else {
    g_free (theme->font_family);
    theme->font_family = g_strdup (find_font_family (key));
  }
}


void
pos_xx_theme_load (PosXxTheme *theme)
{
  g_autofree char *path = pos_xx_theme_config_path ();
  pos_xx_theme_load_from_path (theme, path);
}


void
pos_xx_theme_free (PosXxTheme *theme)
{
  if (theme == NULL)
    return;
  g_free (theme->key);
  g_free (theme->background);
  g_free (theme->surface);
  g_free (theme->surface_alt);
  g_free (theme->border);
  g_free (theme->foreground);
  g_free (theme->muted);
  g_free (theme->accent);
  g_free (theme->font_family);
  memset (theme, 0, sizeof (*theme));
}


#define XX_OR_EMPTY(s) ((s) != NULL ? (s) : "")

/* Quote each comma-separated family like GTK CSS wants. Strips the same
 * characters xx_ui.css.sanitize_font_family removes. */
static char *
css_font_family (const char *family)
{
  g_auto (GStrv) parts = NULL;
  GString *out = g_string_new (NULL);
  gboolean first = TRUE;

  if (family == NULL)
    family = "";
  parts = g_strsplit (family, ",", -1);
  for (int i = 0; parts[i] != NULL; i++) {
    g_autofree char *clean = g_strdup (g_strstrip (parts[i]));
    for (char *p = clean; *p != '\0'; p++) {
      if (*p == '\'' || *p == '"' || *p == '`' || *p == '\\' ||
          *p == ';' || *p == '{' || *p == '}')
        memmove (p, p + 1, strlen (p + 1) + 1), p--;
    }
    g_strstrip (clean);
    if (*clean == '\0')
      continue;
    if (!first)
      g_string_append (out, ", ");
    g_string_append_printf (out, "'%s'", clean);
    first = FALSE;
  }
  if (first)
    g_string_append (out, "'Monospace'");
  return g_string_free (out, FALSE);
}


/* GTK3 override from the xx_ui tokens (DESIGN.md: surface keys, inverted
 * return key, theme font everywhere incl. the suggestion bar, 12 px key
 * radius, no libadwaita blue). Loaded at APPLICATION priority so it wins
 * over the bundled Adwaita stylesheets. */
char *
pos_xx_theme_render_css (const PosXxTheme *theme)
{
  g_autofree char *font = css_font_family (theme->font_family);

  return g_strdup_printf (
    "/* xx-keyboard override from the xx-wm theme, see pos-xx-theme.c */\n"
    "pos-input-surface, pos-osk-widget {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "}\n"
    "pos-key {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "  border: 1px solid %s;\n"
    "  border-radius: 12px;\n"
    "  font-family: %s;\n"
    "  font-weight: 400;\n"
    "}\n"
    "pos-key.pressed {\n"
    "  background-color: %s;\n"
    "}\n"
    "pos-key.sys, pos-key.toggle {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "}\n"
    "pos-key.return {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "}\n"
    "pos-key.return.pressed {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "}\n"
    "pos-completion-bar button, pos-char-popup button,\n"
    "pos-indicator-popup label {\n"
    "  color: %s;\n"
    "  font-family: %s;\n"
    "}\n"
    "pos-shortcuts-bar button {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "  border-radius: 12px;\n"
    "  font-family: %s;\n"
    "}\n"
    "pos-keypad button {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "  font-family: %s;\n"
    "}\n"
    "pos-keypad button.return {\n"
    "  background-color: %s;\n"
    "  color: %s;\n"
    "}\n",
    XX_OR_EMPTY (theme->background),
    XX_OR_EMPTY (theme->foreground),
    XX_OR_EMPTY (theme->surface),
    XX_OR_EMPTY (theme->foreground),
    XX_OR_EMPTY (theme->border),
    font,
    XX_OR_EMPTY (theme->surface_alt),
    XX_OR_EMPTY (theme->surface_alt),
    XX_OR_EMPTY (theme->foreground),
    /* inverted return key: foreground fill, background text */
    XX_OR_EMPTY (theme->foreground),
    XX_OR_EMPTY (theme->background),
    XX_OR_EMPTY (theme->accent),
    XX_OR_EMPTY (theme->background),
    XX_OR_EMPTY (theme->foreground),
    font,
    XX_OR_EMPTY (theme->surface_alt),
    XX_OR_EMPTY (theme->foreground),
    font,
    XX_OR_EMPTY (theme->surface),
    XX_OR_EMPTY (theme->foreground),
    font,
    XX_OR_EMPTY (theme->foreground),
    XX_OR_EMPTY (theme->background));
}


static gboolean
valid_layout_id (const char *text)
{
  g_autofree char *lower = NULL;
  g_auto (GStrv) parts = NULL;

  if (text == NULL)
    return FALSE;
  lower = g_strdup (text);
  if (*g_strstrip (lower) == '\0')
    return FALSE;
  g_strstrip (lower);
  for (char *p = lower; *p != '\0'; p++)
    *p = g_ascii_tolower (*p);
  parts = g_strsplit (lower, "+", 2);
  if (parts[0] == NULL || !g_regex_match_simple ("^[a-z][a-z0-9-]{1,23}$", parts[0], 0, 0))
    return FALSE;
  if (parts[1] != NULL && *parts[1] != '\0' &&
      !g_regex_match_simple ("^[a-z0-9_-]{1,32}$", parts[1], 0, 0))
    return FALSE;
  return TRUE;
}


char *
pos_xx_theme_keyboard_layout_id (void)
{
  g_autofree char *path = pos_xx_theme_config_path ();
  return pos_xx_theme_keyboard_layout_id_from_path (path);
}


char *
pos_xx_theme_keyboard_layout_id_from_path (const char *path)
{
  g_autoptr (JsonParser) parser = NULL;
  g_autoptr (GError) err = NULL;
  const char *value = NULL;

  parser = json_parser_new ();
  if (path != NULL && json_parser_load_from_file (parser, path, &err) &&
      JSON_NODE_HOLDS_OBJECT (json_parser_get_root (parser))) {
    JsonObject *config = json_node_get_object (json_parser_get_root (parser));
    value = config_string (config, "keyboard_layout");
  }
  if (value != NULL) {
    g_autofree char *copy = g_strdup (value);
    if (valid_layout_id (copy)) {
      g_strstrip (copy);
      for (char *p = copy; *p != '\0'; p++)
        *p = g_ascii_tolower (*p);
      return g_steal_pointer (&copy);
    }
  }
  return g_strdup (XX_DEFAULT_LAYOUT_ID);
}


static char *
normalize_layout_id (const char *text)
{
  g_autofree char *copy = NULL;

  if (!valid_layout_id (text))
    return NULL;
  copy = g_strdup (text);
  g_strstrip (copy);
  for (char *p = copy; *p != '\0'; p++)
    *p = g_ascii_tolower (*p);
  return g_steal_pointer (&copy);
}


char **
pos_xx_theme_layout_order (const char *primary)
{
  g_autofree char *norm = normalize_layout_id (primary);
  const char *first = norm != NULL ? norm : XX_DEFAULT_LAYOUT_ID;
  const char *fallbacks[] = { XX_LAYOUT_QWERTY_ID, XX_LAYOUT_PIERCING_ID, NULL };
  GPtrArray *order = g_ptr_array_new_with_free_func (g_free);

  g_ptr_array_add (order, g_strdup (first));
  for (int i = 0; fallbacks[i] != NULL; i++) {
    gboolean seen = FALSE;
    for (guint j = 0; j < order->len; j++) {
      if (g_strcmp0 (g_ptr_array_index (order, j), fallbacks[i]) == 0) {
        seen = TRUE;
        break;
      }
    }
    if (!seen)
      g_ptr_array_add (order, g_strdup (fallbacks[i]));
  }
  g_ptr_array_add (order, NULL);
  return (char **) g_ptr_array_free (order, FALSE);
}


char **
pos_xx_theme_layout_order_from_path (const char *path)
{
  g_autofree char *primary = pos_xx_theme_keyboard_layout_id_from_path (path);
  return pos_xx_theme_layout_order (primary);
}


GFileMonitor *
pos_xx_theme_watch (GCallback callback, gpointer user_data)
{
  g_autofree char *path = pos_xx_theme_config_path ();
  g_autoptr (GFile) file = g_file_new_for_path (path);
  GFileMonitor *monitor;
  g_autoptr (GError) err = NULL;

  monitor = g_file_monitor_file (file, G_FILE_MONITOR_NONE, NULL, &err);
  if (monitor == NULL) {
    /* No config file yet: watch the directory so first creation reloads */
    g_autoptr (GFile) dir = g_file_get_parent (file);
    g_debug ("xx theme: watching directory instead (%s)", err->message);
    if (dir == NULL)
      return NULL;
    monitor = g_file_monitor_directory (dir, G_FILE_MONITOR_NONE, NULL, NULL);
    if (monitor == NULL)
      return NULL;
  }
  g_signal_connect (monitor, "changed", callback, user_data);
  return monitor;
}
