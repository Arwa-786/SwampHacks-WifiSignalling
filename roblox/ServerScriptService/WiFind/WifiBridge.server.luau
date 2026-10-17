-- WifiBridge (Script): the only code that talks to the laptop.
--   * polls GET  /api/sense  ~4x/s  -> WiFi.State / Motion / Receivers
--   * sends POST /api/phase  on every light change, so the booth speaker and
--     LEDs (the hidden player's only cue) follow the Roblox game.
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
WiFi.Booth.Event:Connect(tellBooth)
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
