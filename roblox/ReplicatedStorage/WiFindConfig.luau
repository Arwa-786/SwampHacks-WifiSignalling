-- WiFindConfig (ModuleScript in ReplicatedStorage)
-- Every tunable number for the Roblox side lives here.

return {
	-- Public HTTPS address of the WiFind hub on the Vultr server (server/hub.py).
	-- The booth laptop connects out to it (app.py --hub ...), so this address
	-- never changes, whatever network the laptop is on. No tunnel needed.
	SERVER_URL = "https://64-177-42-5.sslip.io",

	POLL_S = 0.25, -- ~4 polls/s, well under Roblox's 500 requests/min limit
	OFFLINE_AFTER_FAILS = 8, -- ~2 s of failed polls -> show "Wi-Fi offline"

	-- After "red light", nobody is judged for this long. Measured in the first
	-- real round (2026-10-17): Wi-Fi reports "frozen" 2-4 s after red, from the
	-- spoken cue's delay (~0.5-1 s), reaction (~0.5 s), the model's 1 s window +
	-- 3-reading confirmation, and Roblox polling. 1.3 s eliminated a frozen player.
	GRACE_S = 3.0,

	-- Between rounds (Roblox runs this and tells the laptop each step):
	STEP_OUT_S = 8, -- "Hider, step out!" (4 s was too short: people were still walking out)
	SCAN_TIMEOUT_S = 14, -- the laptop's empty-room scan takes ~6 s; give up waiting after this
	HIDE_S = 12, -- "Next hider, climb in" (the first real player was caught still settling in)
	RESULTS_S = 9,
	LOBBY_S = 15, -- lobby wait when the Wi-Fi is offline (online players only)

	-- Red-light catches need this much steady movement, so one twitch or one
	-- glitchy reading doesn't knock anyone out (Wi-Fi: ~3 readings in a row).
	CATCH_HOLD_S = 0.75,
	PLAYER_CATCH_HOLD_S = 0.2, -- keyboard players: forgives letting go of a key a beat late

	-- Who sees the host buttons (START NOW / PAUSE / END ROUND / RESCAN). The
	-- experience's owner and anyone testing in Studio are hosts automatically.
	HOST_USERIDS = {},
	ROUND_S = 90,
	GREEN_RANGE_S = { 2.5, 6 },
	RED_RANGE_S = { 3, 5 },

	-- Whose avatar Wi-Fi controls: the Roblox account signed in on the booth
	-- laptop (Arwa's, the experience owner). 0 = the first player in the server,
	-- which in a public game could be a stranger.
	WIFI_PLAYER_USERID = 11826577726,
	RUNNER_SPEED = 6, -- studs/s the real player's avatar walks while they move
	PLAYER_MOVE_EPS = 0.1, -- MoveDirection magnitude that counts as moving
}
