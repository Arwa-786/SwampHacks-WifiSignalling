-- WifiControl (LocalScript in StarterPlayerScripts): when the server marks this
-- player as the Wi-Fi player (attribute WiFiControlled), their keyboard is
-- switched off for the round and Wi-Fi walks their own avatar instead: it
-- walks toward the gator while the laptop says MOVING and stops otherwise.
-- Runs on the client because a player's avatar is physically simulated by
-- their own client; walking it here also plays the avatar's normal walk
-- animation (Humanoid:Move drives the default Animate script).

local ContextActionService = game:GetService("ContextActionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local player = Players.LocalPlayer
local root = ReplicatedStorage:WaitForChild("WiFind")
local Phase = root:WaitForChild("Phase")
local Paused = root:WaitForChild("Paused")
local State = root:WaitForChild("WiFi"):WaitForChild("State")

local TOWARD_GATOR = Vector3.new(0, 0, -1)
local ROUND_PHASES = { COUNTDOWN = true, GREEN = true, RED = true, RESULTS = true }
-- Keyboard / gamepad movement is swallowed while Wi-Fi drives. (Newer Studio
-- builds have no PlayerScripts.PlayerModule to Disable(), so we sink the input.)
local MOVE_KEYS = {
	Enum.KeyCode.W, Enum.KeyCode.A, Enum.KeyCode.S, Enum.KeyCode.D,
	Enum.KeyCode.Up, Enum.KeyCode.Down, Enum.KeyCode.Left, Enum.KeyCode.Right,
	Enum.KeyCode.Space, Enum.KeyCode.Thumbstick1,
}
local function sink()
	return Enum.ContextActionResult.Sink
end

local wifiDriven = false
-- Bound after the character's input step, so our Move wins every frame.
RunService:BindToRenderStep("WifiControl", Enum.RenderPriority.Character.Value + 1, function()
	local mine = player:GetAttribute("WiFiControlled") == true and ROUND_PHASES[Phase.Value] == true
	if mine ~= wifiDriven then
		wifiDriven = mine
		if mine then
			ContextActionService:BindActionAtPriority("WifiControlSink", sink, false,
				Enum.ContextActionPriority.High.Value, table.unpack(MOVE_KEYS))
		else
			ContextActionService:UnbindAction("WifiControlSink")
		end
	end
	if not mine then
		return
	end
	local char = player.Character
	local humanoid = char and char:FindFirstChildOfClass("Humanoid")
	if humanoid and humanoid.Health > 0 then
		local walking = State.Value == "MOVING" and (Phase.Value == "GREEN" or Phase.Value == "RED")
			and not Paused.Value
		humanoid:Move(walking and TOWARD_GATOR or Vector3.zero, false)
	end
end)
