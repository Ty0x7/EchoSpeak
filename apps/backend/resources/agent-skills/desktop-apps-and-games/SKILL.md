---
name: desktop-apps-and-games
description: How to operate apps and games that draw their own interface (game menus, launchers, canvas apps) with screenshots, clicks and keys, check every step, and recover when an action does nothing. Use for any request to open, set up or play a game, or to drive an app whose buttons aren't listed as named controls.
---

# Operating apps and games on the screen

Games and many launchers draw their buttons themselves, so Windows can't name them:
`desktop_click` by name never finds them, and the screenshot lists no named controls for the
window. You work them like a person does: look, point, click or press, look again.

## The loop for every step

1. **Look.** `take_screenshot` of the app's window (`window_title`), or of the monitor if the
   window is small or you aren't sure which one it is. Note the observation ID and the
   coordinate size it reports.
2. **Decide one step.** Name the thing you'll press and where it is in *this* image, for example
   "Singleplayer, the top button, centre about [435, 230]".
3. **Act.** `computer_action` with that observation ID and one to three actions: a `click` at the
   centre of the button's label, or a `key`.
4. **Check.** Read the change note and the returned screenshot. Did the screen become what you
   expected? Say so to yourself in one line, then go to step 2 with the *new* observation ID.

`vision_qa`, `take_screenshot` and `read_image` only look. Asking them to "click" does nothing.

## When an action changes nothing

Don't repeat it. Try, in this order:

1. Click the middle of the button's text again, with coordinates from the newest screenshot
   (an older screenshot's coordinates may be off, and a moved window invalidates them).
2. Click once on an empty part of the window to focus it, then try again.
3. Use the keyboard: `tab` moves between buttons in most menus, `enter` presses the selected
   one, `esc` goes back or closes a menu, arrow keys move selections.
4. `double_click` the button.

If two different approaches fail, stop and tell the user what you see and what isn't working.

## Menus and setup screens

- Read the whole screen before clicking: the button you need may be lower down or behind a tab.
- Toggle buttons (difficulty, game mode, options) change their label when clicked. Click, check
  the label, and click again until it shows the value you want.
- Text fields: click into the field first, select what's there (`ctrl+a`), then `type`.
- Loading screens: `wait` one to three seconds, then look again. Don't click while it loads.

## Playing a game

- Input is bounded: holds and waits last at most five seconds per action, and a batch at most
  ten. Play in short bursts and look between them.
- Movement keys: `hold` with `w`, `a`, `s`, `d` for a second or two. `space` jumps.
- Turning the camera in games that capture the mouse: `move_relative` with a small `delta`
  such as `[200, 0]`; plain `move` and `click` coordinates don't turn the camera.
- Holding a mouse button (mining, attacking): `hold` with `button: "left"` and a duration.
- `esc` usually pauses and shows a menu: a safe way to get your bearings or the pointer back.
- Real-time play is slow this way. Tell the user what you can do (set things up, reach the game,
  take simple actions) rather than promising to play like a person.

## After it works

If you worked out the steps for a particular app or game (where its buttons are, which keys
it wants), offer to save them as a skill with `propose_skill`, for example
`minecraft-new-world`. Write only the steps that worked.
