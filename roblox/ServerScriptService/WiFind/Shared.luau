-- Shared (ModuleScript): creates the values and remotes the scripts and HUD use.
-- Required by both server scripts; a module runs once, so the folder is made once.
--
-- ReplicatedStorage.WiFind
--   Phase     StringValue  LOBBY / COUNTDOWN / GREEN / RED / RESULTS
--   Message   StringValue  big line on everyone's HUD
--   TimeLeft  NumberValue  seconds left in the round
--   CallRed   RemoteEvent  client -> server: "red light!" button
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
	Message = make("StringValue", "Message", root, "Starting..."),
	TimeLeft = make("NumberValue", "TimeLeft", root, 0),
	CallRed = make("RemoteEvent", "CallRed", root),
	Fx = make("RemoteEvent", "Fx", root),
	WiFi = {
		State = make("StringValue", "State", wifi, "OFFLINE"),
		Motion = make("NumberValue", "Motion", wifi, 0),
		Receivers = make("StringValue", "Receivers", wifi, ""),
		Booth = make("BindableEvent", "Booth", wifi),
	},
}
