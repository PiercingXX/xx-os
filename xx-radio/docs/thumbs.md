# Thumbs up / down (local ratings)

Local-only thumbs that shape what xx-radio shows. The local value is
the truth for this app; YouTube is best effort.

## Store

- One rating per track: up, down, or none (no entry), plus the track's
  artist ids/names and the time it was set.
- File: `ratings.json` next to the other local data (`history.json`,
  `blocked-artists.json`), written atomically (tmp file + rename).
  Each entry is `{rating, track, artists: [{id, name}], engine, at}`.
  Old entries without `artists`/`engine` still read (artists fall back
  to the stored track).
- Kill-hint state: `thumb-kill-suggestions.json` (sorted list of artist
  keys already suggested), written atomically.
- Works always: signed out, offline, and with engines that lack the
  `rate` capability. When signed in with a rating-capable engine the
  player service also forwards the rating to YouTube; a remote failure
  never changes the local value.

## Exact rules

Thumbs down on a song:

- The song id joins the local disliked set.
- It never appears again in suggestions (string suggestions naming the
  song or its artist are dropped), radio/autoplay extension, Quick
  picks, home shelves, mood shelves, charts shelves, library lists
  (except the Thumbs-down list itself), or search's top results
  (`search(kind=None)`). Filtering and re-scoring run in the one
  policy layer (`FilteredEngine` over any engine).
- It stays findable by an explicit search (`search(kind=TRACK)` and
  the other kind-filtered searches) and on its album/playlist/artist
  pages, where it shows like any other song. The Library and Settings
  "Thumbs down" lists mark it as disliked and offer Remove to undo.
- Thumbing down the current song skips it immediately: the service
  removes every queued copy and starts the next song (or stops when
  the queue is empty). The window's local backend does the same.
- Repeated downs on one artist (3 or more distinct songs by the same
  artist id, or the same name when no id is known) lower that artist's
  weight: radio batches and shelf/item orderings rank its songs last
  (`-5` per down in the deterministic score). The app never auto-kills
  an artist. The first time an artist reaches 3 downs it suggests
  `Kill <artist>?` once through the radio_kill confirm flow; the
  suggestion key is persisted so it never repeats, and blocked artists
  are never suggested.

Thumbs up on a song:

- The song joins the local Liked list. Signed in, Library › Liked
  merges the local likes with the YouTube likes, deduplicated by track
  id (local first, engine order kept inside each half).
- Boost: the song scores `+10` and each up on its artist scores `+3`
  per up. Radio batches, home/mood/charts shelves, and the offline mix
  order by that score (stable: engine order breaks ties, no
  randomness). Radio/autoplay seeds are unchanged; what follows them
  prefers upvoted artists, and home shelves with upvoted songs order
  first (best item score wins, stable).
- Keep offline: when Settings › Downloads › "Keep liked songs" is on,
  a thumbs-up enqueues that track's download (best effort).

Clearing: tapping the same thumb again (or Remove in the Thumbs-down
list, or the song menu's Remove entry) deletes the entry. The song
becomes eligible for suggestions again and its artist counts drop.

## Scoring (deterministic)

`xx_music.thumbs.score_track`: `+10` for a liked song, `+3` per up on
any of its artists, `-5` per down on any of its artists. Items and
shelves reuse the same weights (`score_item`, `order_tracks`,
`order_items`, `order_shelves`). No randomness; ties keep engine order.

## UI

- Now Playing footer (Dislike · Playing from · Like) always shows and
  reflects the local rating (`on` class when active); tapping an
  active thumb clears it.
- Song menus always show Thumbs up / Thumbs down (with Remove wording
  when active), independent of the engine's `rate` capability.
- Library has a local "Thumbs down" section (signed in and out) with
  Remove to undo; it is the list's one home (Settings no longer
  repeats it).
- Mini-player: unchanged. Its row (art, title, play/pause, next, cast)
  has no room for a thumb without a redesign, so thumbs live in Now
  Playing and the song menus.
