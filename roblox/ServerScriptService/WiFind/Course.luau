-- Course (ModuleScript): builds the play field from code, so the place file
-- needs no hand-placed parts and can be rebuilt identically anywhere.
--
-- Layout (top-down, players run toward -Z):
--   z = +82  lobby / spawn, behind a see-through gate
--   z = +70  START line
--   z = -60  FINISH line
--   z = -78  the gator (original art), two light towers beside it

local TweenService = game:GetService("TweenService")

local Course = {
	START_Z = 70,
	FINISH_Z = -60,
	GATE_Z = 75,
	WIDTH = 60,
}

local GREEN = Color3.fromRGB(46, 230, 120)
local RED = Color3.fromRGB(255, 59, 72)
local GATOR = Color3.fromRGB(58, 125, 68)
local GATOR_DARK = Color3.fromRGB(37, 84, 46)

local function part(parent, props)
	local p = Instance.new("Part")
	p.Anchored = true
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	for k, v in props do
		p[k] = v
	end
	p.Parent = parent
	return p
end

-- weld a decorative part to the head so it turns with it
local function onHead(head, props, offset)
	props.Anchored = false
	props.CanCollide = false
	props.Massless = true
	props.CFrame = head.CFrame * offset
	local p = part(head.Parent, props)
	local weld = Instance.new("WeldConstraint")
	weld.Part0, weld.Part1 = head, p
	weld.Parent = p
	return p
end

local function label(parent, text, color)
	local gui = Instance.new("SurfaceGui")
	gui.Face = Enum.NormalId.Top
	gui.CanvasSize = Vector2.new(1200, 80)
	gui.Parent = parent
	local t = Instance.new("TextLabel")
	t.Size = UDim2.fromScale(1, 1)
	t.BackgroundTransparency = 1
	t.Font = Enum.Font.GothamBlack
	t.TextScaled = true
	t.TextColor3 = color
	t.Text = text
	t.Parent = gui
end

local function buildGator(folder, z)
	local gator = Instance.new("Model")
	gator.Name = "Gator"
	gator.Parent = folder

	part(gator, { Name = "Pedestal", Size = Vector3.new(14, 2, 14), Position = Vector3.new(0, 1, z),
		Color = Color3.fromRGB(40, 44, 60), Material = Enum.Material.Metal })
	part(gator, { Name = "Body", Shape = Enum.PartType.Ball, Size = Vector3.new(11, 11, 11),
		Position = Vector3.new(0, 7.5, z), Color = GATOR, Material = Enum.Material.SmoothPlastic })
	part(gator, { Name = "Belly", Shape = Enum.PartType.Ball, Size = Vector3.new(8, 9, 8),
		Position = Vector3.new(0, 7, z + 2), Color = Color3.fromRGB(214, 220, 150), Material = Enum.Material.SmoothPlastic })

	-- The head is the only anchored moving part; everything on it is welded.
	-- Local +Z is the face: CFrame yaw 0 = looking at the players (red light).
	local head = part(gator, { Name = "Head", Size = Vector3.new(7, 5, 6),
		CFrame = CFrame.new(0, 15, z), Color = GATOR, Material = Enum.Material.SmoothPlastic })
	onHead(head, { Name = "Snout", Size = Vector3.new(5, 2.4, 6), Color = GATOR,
		Material = Enum.Material.SmoothPlastic }, CFrame.new(0, -1.1, 5.5))
	-- The jaw hangs on a hinge (Motor6D) at its back end so setLight can open it.
	local jaw = part(gator, { Name = "Jaw", Size = Vector3.new(4.6, 1, 5.6), Color = GATOR_DARK,
		Material = Enum.Material.SmoothPlastic, Anchored = false, CanCollide = false, Massless = true,
		CFrame = head.CFrame * CFrame.new(0, -2.6, 5.2) })
	local hinge = Instance.new("Motor6D")
	hinge.Name = "JawHinge"
	hinge.Part0, hinge.Part1 = head, jaw
	hinge.C0 = CFrame.new(0, -2.6, 2.4) -- hinge point on the head
	hinge.C1 = CFrame.new(0, 0, -2.8) -- the jaw's back edge
	hinge.Parent = head
	onHead(head, { Name = "Tongue", Size = Vector3.new(2.4, 0.3, 3.6), Color = Color3.fromRGB(220, 90, 110) },
		CFrame.new(0, -2.0, 4.6))
	for i = 0, 3 do -- ridge of spikes along the head and back
		onHead(head, { Name = "Spike", Shape = Enum.PartType.Wedge, Size = Vector3.new(0.8, 1.4, 1.6),
			Color = GATOR_DARK }, CFrame.new(0, 3.0, -1.6 - i * 1.4))
	end
	for _, side in { -1, 1 } do
		onHead(head, { Name = "Nostril", Shape = Enum.PartType.Ball, Size = Vector3.new(0.7, 0.7, 0.7),
			Color = GATOR_DARK }, CFrame.new(side * 1.3, 0.1, 8.4))
		onHead(head, { Name = "EyeBump", Shape = Enum.PartType.Ball, Size = Vector3.new(2.4, 2.4, 2.4),
			Color = GATOR }, CFrame.new(side * 2, 2.6, 1.5))
		local eye = onHead(head, { Name = "Eye", Shape = Enum.PartType.Ball, Size = Vector3.new(1.6, 1.6, 1.6),
			Color = Color3.new(1, 1, 1), Material = Enum.Material.Neon }, CFrame.new(side * 2, 2.8, 2.4))
		local glow = Instance.new("PointLight")
		glow.Name = "Glow"
		glow.Range = 14
		glow.Brightness = 3
		glow.Enabled = false
		glow.Parent = eye
		for i = -1, 1 do -- teeth along the snout
			onHead(head, { Name = "Tooth", Size = Vector3.new(0.4, 0.7, 0.4), Color = Color3.new(1, 1, 1) },
				CFrame.new(side * 2.3, -2.1, 5 + i * 1.6))
		end
	end
	for _, side in { -1, 1 } do
		onHead(head, { Name = "Pupil", Shape = Enum.PartType.Ball, Size = Vector3.new(0.7, 0.9, 0.7),
			Color = Color3.fromRGB(20, 20, 25) }, CFrame.new(side * 2, 2.8, 3.1))
		-- stubby legs and arms
		part(gator, { Name = "Leg", Shape = Enum.PartType.Ball, Size = Vector3.new(3.4, 3.4, 4.2),
			Position = Vector3.new(side * 4.2, 3.2, z + 1.5), Color = GATOR, Material = Enum.Material.SmoothPlastic })
		part(gator, { Name = "Arm", Shape = Enum.PartType.Ball, Size = Vector3.new(2.4, 2.4, 3.4),
			Position = Vector3.new(side * 5.2, 9, z + 2), Color = GATOR, Material = Enum.Material.SmoothPlastic })
	end
	for i = 1, 4 do -- tail curling out behind the body
		local r = 3.2 - i * 0.6
		part(gator, { Name = "Tail", Shape = Enum.PartType.Ball, Size = Vector3.new(r * 2, r * 2, r * 2.4),
			Position = Vector3.new(i * 1.2, 3 + r * 0.4, z - 4 - i * 2.4), Color = GATOR, Material = Enum.Material.SmoothPlastic })
	end
	for i = 0, 3 do
		part(gator, { Name = "BodySpike", Shape = Enum.PartType.Wedge, Size = Vector3.new(1, 1.6, 2),
			CFrame = CFrame.new(0, 12.6 - i * 1.2, z - 2 - i * 1.3) * CFrame.Angles(math.rad(-30 - i * 12), 0, 0),
			Color = GATOR_DARK })
	end
	return gator, head
end

---------------------------------------------------------------- arena

local CROWD = {
	Color3.fromRGB(255, 99, 132), Color3.fromRGB(255, 205, 86), Color3.fromRGB(75, 192, 192),
	Color3.fromRGB(54, 162, 235), Color3.fromRGB(153, 102, 255), Color3.fromRGB(255, 159, 64),
	Color3.fromRGB(250, 250, 250),
}

local function sign(parent, props, lines, bg, fg)
	local face = props.Face or Enum.NormalId.Front
	props.Face = nil -- not a Part property
	local p = part(parent, props)
	p.Color = bg
	local gui = Instance.new("SurfaceGui")
	gui.Face = face
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = 25
	gui.Parent = p
	local t = Instance.new("TextLabel")
	t.Size = UDim2.fromScale(1, 1)
	t.BackgroundTransparency = 1
	t.Font = Enum.Font.GothamBlack
	t.TextScaled = true
	t.TextColor3 = fg
	t.Text = lines
	t.Parent = gui
	return p
end

-- Stands full of (blocky) fans on both sides, banners, trees and lamps.
local function buildArena(folder, w)
	local arena = Instance.new("Folder")
	arena.Name = "Arena"
	local rng = Random.new(7) -- fixed seed: the same crowd every time
	for _, side in { -1, 1 } do
		for row = 1, 4 do
			local x = side * (w / 2 + 2 + row * 5)
			part(arena, { Name = "Stand", Size = Vector3.new(5, row * 3, 180), Position = Vector3.new(x, row * 1.5, 0),
				Color = row % 2 == 0 and Color3.fromRGB(60, 64, 90) or Color3.fromRGB(78, 84, 116),
				Material = Enum.Material.Concrete })
			for zf = -82, 82, 5 do
				if rng:NextNumber() < 0.75 then
					local fan = part(arena, { Name = "Fan", Size = Vector3.new(1.6, 2.6, 1.6),
						Position = Vector3.new(x, row * 3 + 1.3, zf + rng:NextNumber(-1, 1)),
						Color = CROWD[rng:NextInteger(1, #CROWD)], Material = Enum.Material.SmoothPlastic })
					part(arena, { Name = "FanHead", Shape = Enum.PartType.Ball, Size = Vector3.new(1.4, 1.4, 1.4),
						Position = fan.Position + Vector3.new(0, 2, 0), Color = Color3.fromRGB(255, 214, 170),
						Material = Enum.Material.SmoothPlastic })
				end
			end
		end
		-- banners above the top row, facing the field
		local face = side < 0 and Enum.NormalId.Right or Enum.NormalId.Left
		local bx = side * (w / 2 + 24)
		sign(arena, { Name = "Banner", Size = Vector3.new(1, 8, 44), Position = Vector3.new(bx, 20, -40), Face = face },
			"WiFind", Color3.fromRGB(16, 18, 28), Color3.fromRGB(0, 220, 255))
		sign(arena, { Name = "Banner", Size = Vector3.new(1, 8, 44), Position = Vector3.new(bx, 20, 10), Face = face },
			"Freeze when the Wi-Fi is watching", Color3.fromRGB(255, 59, 72), Color3.new(1, 1, 1))
		sign(arena, { Name = "Banner", Size = Vector3.new(1, 8, 44), Position = Vector3.new(bx, 20, 60), Face = face },
			"Sensed by ESP32 Wi-Fi", Color3.fromRGB(46, 230, 120), Color3.fromRGB(16, 18, 28))
		for zl = -70, 70, 35 do -- warm lamp posts along the wall
			part(arena, { Name = "LampPost", Size = Vector3.new(0.8, 14, 0.8), Position = Vector3.new(side * (w / 2 + 1), 7, zl),
				Color = Color3.fromRGB(40, 44, 60), Material = Enum.Material.Metal })
			local bulb = part(arena, { Name = "Bulb", Shape = Enum.PartType.Ball, Size = Vector3.new(2, 2, 2),
				Position = Vector3.new(side * (w / 2 + 1), 14.5, zl), Color = Color3.fromRGB(255, 220, 160),
				Material = Enum.Material.Neon })
			local l = Instance.new("PointLight")
			l.Range, l.Brightness, l.Color = 30, 1.2, Color3.fromRGB(255, 210, 150)
			l.Parent = bulb
		end
	end
	-- trees behind the gator and around the lobby
	for i, pos in { Vector3.new(-26, 0, -92), Vector3.new(-12, 0, -98), Vector3.new(12, 0, -98), Vector3.new(26, 0, -92),
		Vector3.new(-40, 0, 100), Vector3.new(40, 0, 100), Vector3.new(-50, 0, -100), Vector3.new(50, 0, -100) } do
		local h = 10 + (i % 3) * 3
		part(arena, { Name = "Trunk", Shape = Enum.PartType.Cylinder, Size = Vector3.new(h, 2.4, 2.4),
			CFrame = CFrame.new(pos + Vector3.new(0, h / 2, 0)) * CFrame.Angles(0, 0, math.rad(90)),
			Color = Color3.fromRGB(110, 76, 50), Material = Enum.Material.Wood })
		for j, r in { 9, 7, 5 } do
			part(arena, { Name = "Leaves", Shape = Enum.PartType.Ball, Size = Vector3.new(r, r, r),
				Position = pos + Vector3.new((j - 2) * 1.2, h + j * 2.2, 0), Color = Color3.fromRGB(60, 160 + j * 15, 80),
				Material = Enum.Material.Grass })
		end
	end
	-- a grass ground around everything
	part(arena, { Name = "Grass", Size = Vector3.new(260, 1, 260), Position = Vector3.new(0, -0.48, 0), -- top just above the Baseplate
		Color = Color3.fromRGB(96, 168, 88), Material = Enum.Material.Grass })
	arena.Parent = folder
end

-- Golden-hour light, soft haze and glow: cheap, and makes everything read better.
function Course.lighting()
	local Lighting = game:GetService("Lighting")
	Lighting.ClockTime = 16.8
	Lighting.Brightness = 2.6
	Lighting.EnvironmentDiffuseScale = 0.6
	Lighting.EnvironmentSpecularScale = 0.6
	Lighting.OutdoorAmbient = Color3.fromRGB(140, 130, 150)
	local function ensure(className, props)
		local e = Lighting:FindFirstChildOfClass(className) or Instance.new(className)
		for k, v in props do
			e[k] = v
		end
		e.Parent = Lighting
	end
	ensure("Atmosphere", { Density = 0.28, Haze = 1.2, Color = Color3.fromRGB(255, 220, 190), Glare = 0.4 })
	ensure("BloomEffect", { Intensity = 0.6, Size = 30, Threshold = 1.6 })
	ensure("SunRaysEffect", { Intensity = 0.08, Spread = 0.7 })
end

local function buildTower(folder, x, z)
	part(folder, { Name = "TowerPole", Size = Vector3.new(2, 34, 2), Position = Vector3.new(x, 17, z),
		Color = Color3.fromRGB(40, 44, 60), Material = Enum.Material.Metal })
	local lamp = part(folder, { Name = "Lamp", Shape = Enum.PartType.Ball, Size = Vector3.new(7, 7, 7),
		Position = Vector3.new(x, 37, z), Color = GREEN, Material = Enum.Material.Neon })
	local light = Instance.new("PointLight")
	light.Range = 40
	light.Brightness = 2
	light.Color = GREEN
	light.Parent = lamp
	return lamp
end

-- Destroys any old course and builds a fresh one. Returns handles the game uses.
function Course.build()
	local old = workspace:FindFirstChild("WiFindCourse")
	if old then
		old:Destroy()
	end
	local folder = Instance.new("Folder")
	folder.Name = "WiFindCourse"
	folder.Parent = workspace

	local w = Course.WIDTH
	part(folder, { Name = "Field", Size = Vector3.new(w, 1, 170), Position = Vector3.new(0, -0.45, 0), -- top 0.05 above the Baseplate (no flicker)
		Color = Color3.fromRGB(226, 196, 140), Material = Enum.Material.Sand })
	for _, side in { -1, 1 } do
		part(folder, { Name = "Wall", Size = Vector3.new(2, 8, 190), Position = Vector3.new(side * (w / 2 + 1), 4, 5),
			Color = Color3.fromRGB(235, 120, 160), Material = Enum.Material.SmoothPlastic })
	end
	part(folder, { Name = "BackWall", Size = Vector3.new(w, 8, 2), Position = Vector3.new(0, 4, 96),
		Color = Color3.fromRGB(235, 120, 160), Material = Enum.Material.SmoothPlastic })
	part(folder, { Name = "Lobby", Size = Vector3.new(w, 1, 20), Position = Vector3.new(0, -0.4, 86),
		Color = Color3.fromRGB(70, 80, 110), Material = Enum.Material.SmoothPlastic })

	local startLine = part(folder, { Name = "StartLine", Size = Vector3.new(w, 0.2, 2),
		Position = Vector3.new(0, 0.1, Course.START_Z), Color = Color3.new(1, 1, 1), Material = Enum.Material.Neon })
	label(startLine, "START", Color3.fromRGB(30, 30, 40))
	local finishLine = part(folder, { Name = "FinishLine", Size = Vector3.new(w, 0.2, 3),
		Position = Vector3.new(0, 0.1, Course.FINISH_Z), Color = Color3.fromRGB(255, 214, 10), Material = Enum.Material.Neon })
	label(finishLine, "FINISH", Color3.fromRGB(30, 30, 40))
	-- lane stripes every 10 studs, plus the glowing Wi-Fi lane for the real player
	for zs = Course.FINISH_Z + 5, Course.START_Z - 5, 20 do
		part(folder, { Name = "Stripe", Size = Vector3.new(w, 0.06, 10), Position = Vector3.new(0, 0.03, zs),
			Color = Color3.fromRGB(214, 182, 126), Material = Enum.Material.Sand })
	end
	part(folder, { Name = "WiFiLane", Size = Vector3.new(0.4, 0.08, Course.START_Z - Course.FINISH_Z),
		Position = Vector3.new(-3, 0.05, (Course.START_Z + Course.FINISH_Z) / 2), Color = Color3.fromRGB(0, 220, 255),
		Material = Enum.Material.Neon, Transparency = 0.3 })
	part(folder, { Name = "WiFiLane", Size = Vector3.new(0.4, 0.08, Course.START_Z - Course.FINISH_Z),
		Position = Vector3.new(3, 0.05, (Course.START_Z + Course.FINISH_Z) / 2), Color = Color3.fromRGB(0, 220, 255),
		Material = Enum.Material.Neon, Transparency = 0.3 })
	sign(folder, { Name = "LobbySign", Size = Vector3.new(30, 7, 1), Position = Vector3.new(0, 9, 95) },
		"WiFind · Red Light, Green Light\nthe real player is hidden; Wi-Fi decides if they moved",
		Color3.fromRGB(16, 18, 28), Color3.new(1, 1, 1))
	buildArena(folder, w)

	-- Invisible gate keeps lobby players off the field during a round.
	-- (A visible one fills the bottom of the camera view at the start line.)
	local gate = part(folder, { Name = "Gate", Size = Vector3.new(w, 10, 1), Position = Vector3.new(0, 5, Course.GATE_Z),
		Transparency = 1 })

	-- The Baseplate template has a spawn pad in the middle of our field.
	for _, s in workspace:GetDescendants() do
		if s:IsA("SpawnLocation") then
			s:Destroy()
		end
	end
	local spawn = Instance.new("SpawnLocation")
	spawn.Name = "LobbySpawn"
	spawn.Anchored = true
	spawn.Neutral = true
	spawn.Duration = 0
	spawn.Size = Vector3.new(12, 1, 8)
	spawn.Position = Vector3.new(0, 0.5, 86)
	spawn.Color = Color3.fromRGB(46, 230, 120)
	spawn.Parent = folder

	local gator, head = buildGator(folder, -78)
	-- built at a readable part size, then scaled up so it reads from the start
	-- line ~150 studs away; re-seat it on the ground afterwards
	gator:ScaleTo(1.7)
	local box, size = gator:GetBoundingBox()
	gator:PivotTo(gator:GetPivot() + Vector3.new(0, size.Y / 2 - box.Position.Y, 0))
	local lamps = { buildTower(folder, -20, -78), buildTower(folder, 20, -78) }
	local hinge = head:FindFirstChild("JawHinge")
	Course.lighting()

	return {
		folder = folder,
		gator = gator,
		head = head,
		lamps = lamps,
		gate = gate,
		spawn = spawn,
		headHome = head.CFrame,
		jaw = hinge,
		jawClosed = hinge and hinge.C0, -- after ScaleTo, so offsets are scaled
	}
end

-- Turn the head and recolor the lamps/eyes for a light. Returns the tween.
function Course.setLight(c, isRed)
	local color = isRed and RED or GREEN
	for _, lamp in c.lamps do
		lamp.Color = color
		lamp.PointLight.Color = color
	end
	for _, eye in c.gator:GetDescendants() do
		if eye.Name == "Eye" then
			eye.Color = isRed and RED or Color3.new(1, 1, 1)
			eye.Glow.Color = RED
			eye.Glow.Enabled = isRed
		end
	end
	local yaw = isRed and 0 or math.pi -- 0 faces the players
	local tween = TweenService:Create(c.head, TweenInfo.new(isRed and 0.35 or 0.6, Enum.EasingStyle.Quad),
		{ CFrame = c.headHome * CFrame.Angles(0, yaw, 0) })
	tween:Play()
	if c.jaw then -- open wide when it turns to look at you
		TweenService:Create(c.jaw, TweenInfo.new(0.35, Enum.EasingStyle.Back),
			{ C0 = isRed and c.jawClosed * CFrame.Angles(math.rad(28), 0, 0) or c.jawClosed }):Play()
	end
	return tween
end

return Course
