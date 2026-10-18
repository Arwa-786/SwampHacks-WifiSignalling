-- WifiBridge (Script): the only code that talks to the laptop.
--   * polls GET  /api/sense  ~4x/s  -> WiFi.State / Motion / Receivers
--   * sends POST /api/phase  on every booth step (LOBBY, SCAN, HIDE, COUNTDOWN,
--     GREEN, RED, OUT, WON, PAUSED), so the booth speaker (the hidden player's
--     only cue) follows the Roblox game and the laptop scans when told (SCAN)
--   * after OUT / WON, fetches the AI gator's line (GET /api/ai/last) for the
--     results card
-- Needs Game Settings > Security > "Allow HTTP Requests" turned on.

local HttpService = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage:WaitForChild("WiFindConfig"))
local Shared = require(script.Parent.Shared)
local WiFi = Shared.WiFi

local base = Config.SERVER_URL:gsub("/+$", "")

local function tellBooth(phase)
	task.spawn(function()
		local ok, err = pcall(function()
			HttpService:PostAsync(
				base .. "/api/phase",
				HttpService:JSONEncode({ phase = phase }),
				Enum.HttpContentType.ApplicationJson
			)
		end)
		if not ok then
			warn("[WiFind] could not tell the booth the light:", err)
		end
	end)
end
-- The laptop writes the gator's line ~1-3 s after a catch or win; wait for a
-- NEW line (not the previous round's) and show it on the results card.
local function fetchGatorLine()
	task.spawn(function()
		local before = nil
		pcall(function()
			before = HttpService:JSONDecode(HttpService:GetAsync(base .. "/api/ai/last", true)).line
		end)
		for _ = 1, 8 do
			task.wait(1)
			local ok, last = pcall(function()
				return HttpService:JSONDecode(HttpService:GetAsync(base .. "/api/ai/last", true))
			end)
			if ok and last.line and last.line ~= before then
				Shared.GatorLine.Value = last.line
				return
			end
		end
	end)
end

WiFi.Booth.Event:Connect(function(phase)
	tellBooth(phase)
	if phase == "OUT" or phase == "WON" then
		fetchGatorLine()
	end
end)
-- GameLoop may announce the first LOBBY before this script connected; send the
-- current phase once so the laptop still runs its first room scan.
tellBooth(Shared.Phase.Value)

local fails = 0
while true do
	local started = os.clock()
	local ok, result = pcall(function()
		return HttpService:JSONDecode(HttpService:GetAsync(base .. "/api/sense", true))
	end)
	if ok then
		if fails >= Config.OFFLINE_AFTER_FAILS then
			print("[WiFind] laptop reachable again")
		end
		fails = 0
		WiFi.State.Value = result.state
		WiFi.Motion.Value = result.motion or 0
		WiFi.Receivers.Value = table.concat(result.receivers or {}, ",")
	else
		fails += 1
		if fails == Config.OFFLINE_AFTER_FAILS then
			warn("[WiFind] laptop unreachable, Wi-Fi offline:", result)
			WiFi.State.Value = "OFFLINE"
			WiFi.Motion.Value = 0
		end
	end
	task.wait(math.max(0, Config.POLL_S - (os.clock() - started)))
end
