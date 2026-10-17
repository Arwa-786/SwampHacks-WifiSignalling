-- Effects (ModuleScript): world effects everyone sees, made on the server.
--
-- wifiAura draws what the Wi-Fi is sensing, around the real player's avatar:
--   MOVING  fast cyan ripples: the radio signal is being disturbed
--   STILL   a slow soft pulse: "still there", present but not moving
-- That's the point of the demo made visible: the game isn't watching a
-- camera, it's reacting to the Wi-Fi.

local Debris = game:GetService("Debris")
local TweenService = game:GetService("TweenService")

local Effects = {}

local CYAN = Color3.fromRGB(0, 220, 255)
local SOFT = Color3.fromRGB(180, 255, 220)
local RED = Color3.fromRGB(255, 59, 72)

local function ring(pos: Vector3, color: Color3, from: number, to: number, duration: number, transparency: number)
	local p = Instance.new("Part")
	p.Anchored, p.CanCollide, p.CanQuery, p.CanTouch, p.CastShadow = true, false, false, false, false
	p.Shape = Enum.PartType.Cylinder -- a flat disc once turned on its side
	p.Material = Enum.Material.Neon
	p.Color = color
	p.Transparency = transparency
	p.Size = Vector3.new(0.15, from, from)
	p.CFrame = CFrame.new(pos.X, 0.25, pos.Z) * CFrame.Angles(0, 0, math.rad(90))
	p.Parent = workspace
	TweenService:Create(p, TweenInfo.new(duration, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), {
		Size = Vector3.new(0.15, to, to),
		Transparency = 1,
	}):Play()
	Debris:AddItem(p, duration + 0.1)
end

local lastAura = 0
function Effects.wifiAura(pos: Vector3, sensed: string)
	local now = os.clock()
	if sensed == "MOVING" and now - lastAura > 0.3 then
		lastAura = now
		ring(pos, CYAN, 2, 12, 0.9, 0.6)
	elseif sensed == "STILL" and now - lastAura > 1.6 then
		lastAura = now
		ring(pos, SOFT, 3, 8, 1.4, 0.75)
	end
end

function Effects.caught(pos: Vector3)
	ring(pos, RED, 2, 30, 1.2, 0.1)
	task.delay(0.15, ring, pos, RED, 2, 22, 1.0, 0.3)
end

local CONFETTI = {
	Color3.fromRGB(255, 99, 132), Color3.fromRGB(255, 205, 86), Color3.fromRGB(75, 192, 192),
	Color3.fromRGB(54, 162, 235), Color3.fromRGB(153, 102, 255), Color3.fromRGB(46, 230, 120),
}

function Effects.confetti(pos: Vector3)
	local rng = Random.new()
	for _ = 1, 70 do
		local p = Instance.new("Part")
		p.Size = Vector3.new(0.5, 0.1, 0.8)
		p.CanCollide, p.CanQuery, p.CanTouch, p.CastShadow = false, false, false, false
		p.Material = Enum.Material.Neon
		p.Color = CONFETTI[rng:NextInteger(1, #CONFETTI)]
		p.CFrame = CFrame.new(pos + Vector3.new(0, 3, 0)) * CFrame.Angles(rng:NextNumber(0, 6), rng:NextNumber(0, 6), 0)
		p.AssemblyLinearVelocity = Vector3.new(rng:NextNumber(-18, 18), rng:NextNumber(25, 45), rng:NextNumber(-18, 18))
		p.Parent = workspace
		Debris:AddItem(p, 4)
	end
	ring(pos, Color3.fromRGB(255, 214, 10), 2, 24, 1.2, 0.2)
end

return Effects
