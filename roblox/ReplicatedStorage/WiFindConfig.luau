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

	-- the laptop's auto room scan (4 s to step out + 6 s scan) plus ~12 s for
	-- the next player to walk in and settle (4 s was too short: the first real
	-- player was caught still settling in)
	INTERMISSION_S = 22,
	ROUND_S = 90,
	GREEN_RANGE_S = { 2.5, 6 },
	RED_RANGE_S = { 3, 5 },
	RED_CALL_COOLDOWN_S = 8, -- how often online players may call red light

	-- Whose avatar Wi-Fi controls: the Roblox account signed in on the booth
	-- laptop. 0 = the first player in the server (you, in a Studio test).
	WIFI_PLAYER_USERID = 0,
	RUNNER_SPEED = 6, -- studs/s the real player's avatar walks while they move
	PLAYER_MOVE_EPS = 0.1, -- MoveDirection magnitude that counts as moving
}
