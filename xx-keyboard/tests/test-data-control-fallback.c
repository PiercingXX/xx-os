/*
 * Copyright (C) 2026 The XX-Phone Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Tests for the older-phoc compatibility policy (pos-wayland.h): which
 * data-control protocol gets picked and which Wayland globals are
 * genuinely required to type. Headless on purpose: the helpers are
 * pure, so this runs in plain `meson test` with no display.
 *
 * Background: phoc 0.44.0+furios (FuriPhone FLX1) only offers
 * `zwlr_data_control_manager_v1` (v2), not `ext_data_control_manager_v1`,
 * which used to abort startup via pos_wayland_has_wl_protcols().
 */

#include "pos-wayland.h"

#include <glib.h>


static void
test_pick_prefers_ext (void)
{
  g_assert_cmpint (pos_data_control_pick (TRUE, TRUE), ==, POS_DATA_CONTROL_EXT);
  g_assert_cmpint (pos_data_control_pick (TRUE, FALSE), ==, POS_DATA_CONTROL_EXT);
}


static void
test_pick_falls_back_to_wlr (void)
{
  /* The FLX1 case: only the wlr variant is offered */
  g_assert_cmpint (pos_data_control_pick (FALSE, TRUE), ==, POS_DATA_CONTROL_WLR);
}


static void
test_pick_none_without_either (void)
{
  g_assert_cmpint (pos_data_control_pick (FALSE, FALSE), ==, POS_DATA_CONTROL_NONE);
}


static void
test_required_all_present (void)
{
  g_assert_true (pos_wayland_have_required (TRUE, TRUE, TRUE, TRUE));
}


static void
test_required_each_missing_blocks (void)
{
  g_assert_false (pos_wayland_have_required (FALSE, TRUE, TRUE, TRUE));
  g_assert_false (pos_wayland_have_required (TRUE, FALSE, TRUE, TRUE));
  g_assert_false (pos_wayland_have_required (TRUE, TRUE, FALSE, TRUE));
  g_assert_false (pos_wayland_have_required (TRUE, TRUE, TRUE, FALSE));
  g_assert_false (pos_wayland_have_required (FALSE, FALSE, FALSE, FALSE));
}


int
main (int argc, char *argv[])
{
  g_test_init (&argc, &argv, NULL);

  g_test_add_func ("/pos/wayland/data-control-prefers-ext", test_pick_prefers_ext);
  g_test_add_func ("/pos/wayland/data-control-falls-back-to-wlr", test_pick_falls_back_to_wlr);
  g_test_add_func ("/pos/wayland/data-control-none-without-either", test_pick_none_without_either);
  g_test_add_func ("/pos/wayland/required-all-present", test_required_all_present);
  g_test_add_func ("/pos/wayland/required-each-missing-blocks", test_required_each_missing_blocks);

  return g_test_run ();
}
