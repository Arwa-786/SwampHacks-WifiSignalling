-- Shared (ModuleScript): creates the values and remotes the scripts and HUD use.
-- Required by both server scripts; a module runs once, so the folder is made once.
--
-- ReplicatedStorage.WiFind
--   Phase     StringValue  LOBBY / SCAN / HIDE / COUNTDOWN / GREEN / RED / RESULTS
--   Paused    BoolValue    host paused the game (nobody is judged, avatars stop)
--   Results   StringValue  JSON for the results card: {title, sub, rows = {{label, value}}, caught}
--   GatorLine StringValue  the AI gator's line about the last round (from the laptop)
--   Host      RemoteEvent  client -> server: host buttons ("start" | "pause" | "end" | "rescan")
--   Message   StringValue  big line on everyone's HUD
--   TimeLeft  NumberValue  seconds left in the round
--   Fx        RemoteEvent  server -> clients: ("caught" | "won", character) for sounds + camera
--   WiFi/State      StringValue  MOVING / STILL / EMPTY / UNCALIBRATED / OFFLINE
--   WiFi/Motion     NumberValue  motion / threshold (1.0 = just counts as moving)
--   WiFi/Receivers  StringValue  per-receiver states, comma separated
--   WiFi/Booth      BindableEvent  game -> bridge: tell the booth the light

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local function make(className, name, parent, value)
	local inst = Instance.new(className)
	inst.Name = name
	if value ~= nil then
		inst.Value = value
	end
	inst.Parent = parent
	return inst
end

local root = make("Folder", "WiFind", ReplicatedStorage)
local wifi = make("Folder", "WiFi", root)

return {
	Phase = make("StringValue", "Phase", root, "LOBBY"),
	Paused = make("BoolValue", "Paused", root, false),
	Results = make("StringValue", "Results", root, ""),
	GatorLine = make("StringValue", "GatorLine", root, ""),
	Host = make("RemoteEvent", "Host", root),
	Message = make("StringValue", "Message", root, "Starting..."),
	TimeLeft = make("NumberValue", "TimeLeft", root, 0),
	Fx = make("RemoteEvent", "Fx", root),
	WiFi = {
		State = make("StringValue", "State", wifi, "OFFLINE"),
		Motion = make("NumberValue", "Motion", wifi, 0),
		Receivers = make("StringValue", "Receivers", wifi, ""),
		Booth = make("BindableEvent", "Booth", wifi),
	},
}
