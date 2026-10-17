/*
 * Copyright (C) 2026 The XX-Phone Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Tests for the xx-wm theme bridge (pos-xx-theme.c): preset parsing and
 * the amoled fallback for missing or invalid configs. Headless on
 * purpose: no Gtk init, so this runs in plain `meson test`.
 */

#include "pos-xx-theme.h"

#include <glib.h>


static char *
write_config (const char *contents)
{
  g_autofree char *dir = g_dir_make_tmp ("xx-theme-test-XXXXXX", NULL);
  char *path;

  g_assert_nonnull (dir);
  path = g_build_filename (dir, "config.json", NULL);
  g_assert_true (g_file_set_contents (path, contents, -1, NULL));
  return path;
}


static void
test_missing_falls_back_to_amoled (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *css = NULL;

  pos_xx_theme_load_from_path (&theme, "/nonexistent/config.json");
  g_assert_cmpstr (theme.key, ==, "amoled");
  g_assert_cmpstr (theme.background, ==, "#000000");
  g_assert_cmpstr (theme.surface, ==, "#111111");
  g_assert_cmpstr (theme.foreground, ==, "#f4f4f4");
  g_assert_true (theme.dark);

  css = pos_xx_theme_render_css (&theme);
  g_assert_nonnull (g_strstr_len (css, -1, "#111111"));
  g_assert_nonnull (g_strstr_len (css, -1, "border-radius: 12px"));
  /* No libadwaita blue anywhere in the override */
  g_assert_null (g_strstr_len (css, -1, "3584e4"));

  pos_xx_theme_free (&theme);
}


static void
test_invalid_falls_back_to_amoled (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config ("{ not json");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "amoled");
  g_assert_cmpstr (theme.background, ==, "#000000");
  pos_xx_theme_free (&theme);
}


static void
test_non_object_falls_back_to_amoled (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config ("[1, 2]");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "amoled");
  pos_xx_theme_free (&theme);
}


static void
test_unknown_preset_falls_back_to_amoled (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config ("{\"theme\": \"neon\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "amoled");
  g_assert_cmpstr (theme.background, ==, "#000000");
  pos_xx_theme_free (&theme);
}


static void
test_preset_parsing (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config (
    "{\"theme\": \"ocean\", \"font\": \"space-mono\"}");
  g_autofree char *css = NULL;

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "ocean");
  g_assert_cmpstr (theme.background, ==, "#0F1C2E");
  g_assert_cmpstr (theme.surface, ==, "#182a42");
  g_assert_cmpstr (theme.surface_alt, ==, "#203856");
  g_assert_cmpstr (theme.border, ==, "#34506e");
  g_assert_cmpstr (theme.foreground, ==, "#edf4f7");
  g_assert_cmpstr (theme.font_family, ==, "Space Mono, Monospace");
  g_assert_true (theme.dark);

  /* The suggestion bar and keys render in the theme font */
  css = pos_xx_theme_render_css (&theme);
  g_assert_nonnull (g_strstr_len (css, -1, "'Space Mono', 'Monospace'"));
  g_assert_nonnull (g_strstr_len (css, -1, "pos-completion-bar button"));
  /* Inverted return key: foreground fill, background text */
  g_assert_nonnull (g_strstr_len (css, -1, "pos-key.return"));

  pos_xx_theme_free (&theme);
}


static void
test_light_preset_is_not_dark (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config ("{\"theme\": \"paper\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "paper");
  g_assert_false (theme.dark);
  pos_xx_theme_free (&theme);
}


static void
test_custom_dark (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config (
    "{\"theme\": \"custom\", \"custom_background\": \"#10261B\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "custom");
  g_assert_cmpstr (theme.background, ==, "#10261B");
  /* Dark custom background keeps the amoled text colours */
  g_assert_cmpstr (theme.foreground, ==, "#f4f4f4");
  g_assert_true (theme.dark);
  pos_xx_theme_free (&theme);
}


static void
test_custom_light (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config (
    "{\"theme\": \"custom\", \"custom_background\": \"#F3EEE2\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "custom");
  g_assert_cmpstr (theme.foreground, ==, "#151515");
  g_assert_false (theme.dark);
  pos_xx_theme_free (&theme);
}


static void
test_custom_garbage_falls_back (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config (
    "{\"theme\": \"custom\", \"custom_background\": \"red\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.key, ==, "amoled");
  pos_xx_theme_free (&theme);
}


static void
test_custom_font (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config (
    "{\"font\": \"custom\", \"custom_font_family\": \"My Font\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.font_family, ==, "My Font");
  pos_xx_theme_free (&theme);
}


static void
test_unknown_font_falls_back (void)
{
  PosXxTheme theme = { 0 };
  g_autofree char *path = write_config ("{\"font\": \"comic-sans\"}");

  pos_xx_theme_load_from_path (&theme, path);
  g_assert_cmpstr (theme.font_family, ==,
                   "JetBrainsMono Nerd Font, JetBrains Mono, Monospace");
  pos_xx_theme_free (&theme);
}


static void
test_keyboard_layout_id (void)
{
  /* Never touch the developer's real config: drive the reader with
   * explicit paths only. */
  g_autofree char *missing =
    pos_xx_theme_keyboard_layout_id_from_path ("/nonexistent/config.json");
  g_autofree char *garbage_path = write_config ("{\"keyboard_layout\": 42}");
  g_autofree char *garbage =
    pos_xx_theme_keyboard_layout_id_from_path (garbage_path);
  g_autofree char *custom_path = write_config ("{\"keyboard_layout\": \"de\"}");
  g_autofree char *custom =
    pos_xx_theme_keyboard_layout_id_from_path (custom_path);
  g_autofree char *variant_path =
    write_config ("{\"keyboard_layout\": \"us+dvorak\"}");
  g_autofree char *variant =
    pos_xx_theme_keyboard_layout_id_from_path (variant_path);

  g_assert_cmpstr (missing, ==, "us");
  g_assert_cmpstr (garbage, ==, "us");
  g_assert_cmpstr (custom, ==, "de");
  g_assert_cmpstr (variant, ==, "us+dvorak");
}


static void
test_keyboard_layout_piercing (void)
{
  g_autofree char *path = write_config ("{\"keyboard_layout\": \"piercing-num\"}");
  g_autofree char *id = pos_xx_theme_keyboard_layout_id_from_path (path);

  g_assert_cmpstr (id, ==, "piercing-num");
}


static void
assert_order (const char *primary, const char * const *expected)
{
  g_auto (GStrv) order = pos_xx_theme_layout_order (primary);
  int len = 0;

  for (; expected[len] != NULL; len++);
  g_assert_cmpint (g_strv_length (order), ==, len);
  for (int i = 0; i < len; i++)
    g_assert_cmpstr (order[i], ==, expected[i]);
}


static void
test_layout_order (void)
{
  const char *colemak[] = { "us+colemak", "us", "piercing-num", NULL };
  const char *qwerty[] = { "us", "piercing-num", NULL };
  const char *piercing[] = { "piercing-num", "us", NULL };
  const char *fallback[] = { "us", "piercing-num", NULL };

  assert_order ("us+colemak", colemak);
  assert_order ("us", qwerty);
  assert_order ("piercing-num", piercing);
  assert_order ("US", qwerty);
  assert_order ("garbage!!", fallback);
  assert_order (NULL, fallback);
}


static void
test_layout_order_from_path (void)
{
  g_autofree char *path = write_config ("{\"keyboard_layout\": \"piercing-num\"}");
  g_auto (GStrv) order = pos_xx_theme_layout_order_from_path (path);

  g_assert_cmpint (g_strv_length (order), ==, 2);
  g_assert_cmpstr (order[0], ==, "piercing-num");
  g_assert_cmpstr (order[1], ==, "us");
}


static void
test_config_path_shape (void)
{
  g_autofree char *path = pos_xx_theme_config_path ();
  g_assert_true (g_str_has_suffix (path, "/xx-wm/config.json"));
}


int
main (int argc, char *argv[])
{
  g_test_init (&argc, &argv, NULL);

  g_test_add_func ("/pos/xx-theme/missing-falls-back-to-amoled",
                   test_missing_falls_back_to_amoled);
  g_test_add_func ("/pos/xx-theme/invalid-falls-back-to-amoled",
                   test_invalid_falls_back_to_amoled);
  g_test_add_func ("/pos/xx-theme/non-object-falls-back-to-amoled",
                   test_non_object_falls_back_to_amoled);
  g_test_add_func ("/pos/xx-theme/unknown-preset-falls-back-to-amoled",
                   test_unknown_preset_falls_back_to_amoled);
  g_test_add_func ("/pos/xx-theme/preset-parsing", test_preset_parsing);
  g_test_add_func ("/pos/xx-theme/light-preset-is-not-dark",
                   test_light_preset_is_not_dark);
  g_test_add_func ("/pos/xx-theme/custom-dark", test_custom_dark);
  g_test_add_func ("/pos/xx-theme/custom-light", test_custom_light);
  g_test_add_func ("/pos/xx-theme/custom-garbage-falls-back",
                   test_custom_garbage_falls_back);
  g_test_add_func ("/pos/xx-theme/custom-font", test_custom_font);
  g_test_add_func ("/pos/xx-theme/unknown-font-falls-back",
                   test_unknown_font_falls_back);
  g_test_add_func ("/pos/xx-theme/keyboard-layout-id",
                   test_keyboard_layout_id);
  g_test_add_func ("/pos/xx-theme/keyboard-layout-piercing",
                   test_keyboard_layout_piercing);
  g_test_add_func ("/pos/xx-theme/layout-order", test_layout_order);
  g_test_add_func ("/pos/xx-theme/layout-order-from-path",
                   test_layout_order_from_path);
  g_test_add_func ("/pos/xx-theme/config-path-shape", test_config_path_shape);

  return g_test_run ();
}
