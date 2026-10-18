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

---------------------------------------------------------------- swamp

-- the same palette as the web pages (Sweetie 16 + browns)
local WOOD = Color3.fromRGB(139, 90, 60)
local WOOD_DARK = Color3.fromRGB(92, 58, 40)
local DECK = Color3.fromRGB(184, 132, 86) -- the boardwalk: lighter, so the track reads in dusk light
local CREAM = Color3.fromRGB(255, 244, 214)
local MOSS = Color3.fromRGB(106, 143, 63)
local LEAF = Color3.fromRGB(56, 183, 100)
local LEAF_DARK = Color3.fromRGB(30, 77, 43)
local WATER = Color3.fromRGB(37, 113, 121)

-- A wooden sign on two posts, text facing the given side.
local function sign(parent, props, lines, bg, fg)
	local face = props.Face or Enum.NormalId.Front
	props.Face = nil -- not a Part property
	local p = part(parent, props)
	p.Color = bg
	p.Material = Enum.Material.Wood
	local gui = Instance.new("SurfaceGui")
	gui.Face = face
	gui.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
	gui.PixelsPerStud = 25
	gui.Parent = p
	local t = Instance.new("TextLabel")
	t.Size = UDim2.fromScale(1, 1)
	t.BackgroundTransparency = 1
	t.Font = Enum.Font.FredokaOne
	t.TextScaled = true
	t.TextColor3 = fg
	t.TextStrokeTransparency = 0.4
	t.Text = lines
	t.Parent = gui
	for _, dx in { -0.4, 0.4 } do -- posts under it
		local off = p.CFrame:VectorToWorldSpace(Vector3.new(dx * p.Size.X, 0, 0))
		if math.abs(p.Size.Z) > math.abs(p.Size.X) then
			off = p.CFrame:VectorToWorldSpace(Vector3.new(0, 0, dx * p.Size.Z))
		end
		local bottom = p.Position.Y - p.Size.Y / 2
		part(parent, { Name = "SignPost", Size = Vector3.new(0.8, bottom + 0.5, 0.8),
			Position = Vector3.new(p.Position.X + off.X, (bottom + 0.5) / 2 - 0.5, p.Position.Z + off.Z), Color = WOOD_DARK,
			Material = Enum.Material.Wood })
	end
	return p
end

-- A bald cypress: flared trunk, flat layered canopy, Spanish moss hanging down.
local function cypress(parent, pos, h, rng)
	part(parent, { Name = "TrunkBase", Shape = Enum.PartType.Cylinder, Size = Vector3.new(3, 5, 5),
		CFrame = CFrame.new(pos + Vector3.new(0, 1.5, 0)) * CFrame.Angles(0, 0, math.rad(90)), Color = WOOD_DARK,
		Material = Enum.Material.Wood })
	part(parent, { Name = "Trunk", Shape = Enum.PartType.Cylinder, Size = Vector3.new(h, 2.2, 2.2),
		CFrame = CFrame.new(pos + Vector3.new(0, h / 2, 0)) * CFrame.Angles(0, 0, math.rad(90)), Color = WOOD,
		Material = Enum.Material.Wood })
	for k = 0, 2 do -- flat canopy layers, widest at the bottom
		local r = 13 - k * 3
		part(parent, { Name = "Canopy", Shape = Enum.PartType.Cylinder, Size = Vector3.new(2.4, r, r),
			CFrame = CFrame.new(pos + Vector3.new(rng:NextNumber(-1, 1), h - 1 + k * 2.4, rng:NextNumber(-1, 1)))
				* CFrame.Angles(0, 0, math.rad(90)), Color = k % 2 == 0 and LEAF_DARK or LEAF, Material = Enum.Material.Grass })
	end
	for _ = 1, 6 do -- moss strands
		local a = rng:NextNumber(0, math.pi * 2)
		local len = rng:NextNumber(2.5, 5)
		part(parent, { Name = "Moss", Size = Vector3.new(0.3, len, 0.3), CanCollide = false,
			Position = pos + Vector3.new(math.cos(a) * 4.5, h - 1.5 - len / 2, math.sin(a) * 4.5), Color = MOSS,
			Material = Enum.Material.Grass })
	end
end

local function cattails(parent, pos, rng)
	for _ = 1, 4 do
		local h = rng:NextNumber(3, 5.5)
		local p = pos + Vector3.new(rng:NextNumber(-1.2, 1.2), 0, rng:NextNumber(-1.2, 1.2))
		part(parent, { Name = "Reed", Size = Vector3.new(0.2, h, 0.2), CanCollide = false, Position = p + Vector3.new(0, h / 2, 0),
			Color = MOSS, Material = Enum.Material.Grass })
		part(parent, { Name = "Cattail", Shape = Enum.PartType.Cylinder, Size = Vector3.new(1.2, 0.5, 0.5), CanCollide = false,
			CFrame = CFrame.new(p + Vector3.new(0, h - 0.4, 0)) * CFrame.Angles(0, 0, math.rad(90)), Color = WOOD_DARK,
			Material = Enum.Material.Fabric })
	end
end

local function lantern(parent, pos)
	part(parent, { Name = "LanternPost", Size = Vector3.new(0.8, 10, 0.8), Position = pos + Vector3.new(0, 5, 0), Color = WOOD_DARK,
		Material = Enum.Material.Wood })
	local glass = part(parent, { Name = "Lantern", Size = Vector3.new(1.4, 1.8, 1.4), Position = pos + Vector3.new(0, 10.5, 0),
		Color = Color3.fromRGB(255, 205, 117), Material = Enum.Material.Neon })
	local l = Instance.new("PointLight")
	l.Range, l.Brightness, l.Color = 24, 1.4, Color3.fromRGB(255, 200, 130)
	l.Parent = glass
end

-- Swamp water, cypress trees, lily pads, cattails, lanterns, fireflies, a camp
-- by the lobby dock and wooden signs: the same world as the web pages.
local function buildSwamp(folder, w)
	local swamp = Instance.new("Folder")
	swamp.Name = "Swamp"
	local rng = Random.new(7) -- fixed seed: the same swamp every time
	part(swamp, { Name = "Water", Size = Vector3.new(320, 1, 320), Position = Vector3.new(0, -0.9, 0), CanCollide = false,
		Color = WATER, Material = Enum.Material.Glass, Transparency = 0.15, Reflectance = 0.15 })
	for _, side in { -1, 1 } do
		-- grassy banks a little way off the boardwalk, with trees on them
		part(swamp, { Name = "Bank", Size = Vector3.new(40, 1.2, 220), Position = Vector3.new(side * (w / 2 + 38), -0.2, 0),
			Color = Color3.fromRGB(56, 120, 60), Material = Enum.Material.Grass })
		for z = -90, 90, 26 do
			cypress(swamp, Vector3.new(side * (w / 2 + rng:NextNumber(22, 50)), 0, z + rng:NextNumber(-6, 6)), rng:NextNumber(16, 24), rng)
		end
		for z = -80, 80, 12 do -- cattails along the water's edge, lily pads on the water
			cattails(swamp, Vector3.new(side * (w / 2 + rng:NextNumber(3, 14)), -0.4, z + rng:NextNumber(-4, 4)), rng)
			local r = rng:NextNumber(3, 6)
			part(swamp, { Name = "LilyPad", Shape = Enum.PartType.Cylinder, Size = Vector3.new(0.2, r, r), CanCollide = false,
				CFrame = CFrame.new(side * (w / 2 + rng:NextNumber(4, 16)), -0.35, z + 6) * CFrame.Angles(0, 0, math.rad(90)),
				Color = LEAF, Material = Enum.Material.Grass })
		end
		for z = -70, 70, 35 do
			lantern(swamp, Vector3.new(side * (w / 2 + 1.5), 0, z))
		end
		-- wooden signs facing the boardwalk
		local face = side < 0 and Enum.NormalId.Right or Enum.NormalId.Left
		sign(swamp, { Name = "Sign", Size = Vector3.new(1, 6, 28), Position = Vector3.new(side * (w / 2 + 9), 7, side < 0 and -30 or 30),
			Face = face }, side < 0 and "Freeze when the Wi-Fi is watching" or "Sensed by ESP32 Wi-Fi\nno cameras", WOOD, CREAM)
	end
	-- fireflies over the water
	for _ = 1, 40 do
		local fly = part(swamp, { Name = "Firefly", Shape = Enum.PartType.Ball, Size = Vector3.new(0.35, 0.35, 0.35), CanCollide = false,
			Position = Vector3.new(rng:NextNumber(-90, 90), rng:NextNumber(2, 12), rng:NextNumber(-100, 100)),
			Color = Color3.fromRGB(255, 240, 150), Material = Enum.Material.Neon })
		local l = Instance.new("PointLight")
		l.Range, l.Brightness, l.Color = 4, 0.8, Color3.fromRGB(255, 240, 150)
		l.Parent = fly
	end
	-- the camp by the lobby: a tent and a real campfire
	local camp = Vector3.new(-w / 2 - 14, 0.4, 88)
	part(swamp, { Name = "Tent", Shape = Enum.PartType.Wedge, Size = Vector3.new(7, 6, 5),
		CFrame = CFrame.new(camp + Vector3.new(-4, 3, 0)), Color = Color3.fromRGB(239, 125, 87), Material = Enum.Material.Fabric })
	part(swamp, { Name = "Tent", Shape = Enum.PartType.Wedge, Size = Vector3.new(7, 6, 5),
		CFrame = CFrame.new(camp + Vector3.new(-4, 3, -5)) * CFrame.Angles(0, math.pi, 0), Color = Color3.fromRGB(255, 205, 117),
		Material = Enum.Material.Fabric })
	local logs = part(swamp, { Name = "Campfire", Size = Vector3.new(3, 0.8, 3), Position = camp + Vector3.new(6, 0.4, -2),
		Color = WOOD_DARK, Material = Enum.Material.Wood })
	local fire = Instance.new("Fire")
	fire.Size, fire.Heat = 5, 9
	fire.Parent = logs
	local smoke = Instance.new("Smoke")
	smoke.Opacity, smoke.RiseVelocity, smoke.Size = 0.08, 3, 2
	smoke.Parent = logs
	local glow = Instance.new("PointLight")
	glow.Range, glow.Brightness, glow.Color = 20, 2, Color3.fromRGB(255, 160, 80)
	glow.Parent = logs
	swamp.Parent = folder
end

-- Swamp dusk: low warm sun, a green-tinted haze, glow on lanterns and fireflies.
function Course.lighting()
	local Lighting = game:GetService("Lighting")
	Lighting.ClockTime = 17.6
	Lighting.Brightness = 2.2
	Lighting.EnvironmentDiffuseScale = 0.6
	Lighting.EnvironmentSpecularScale = 0.6
	Lighting.OutdoorAmbient = Color3.fromRGB(120, 130, 140)
	local function ensure(className, props)
		local e = Lighting:FindFirstChildOfClass(className) or Instance.new(className)
		for k, v in props do
			e[k] = v
		end
		e.Parent = Lighting
	end
	ensure("Atmosphere", { Density = 0.32, Haze = 1.6, Color = Color3.fromRGB(220, 230, 190), Decay = Color3.fromRGB(120, 140, 110),
		Glare = 0.3 })
	ensure("BloomEffect", { Intensity = 0.8, Size = 30, Threshold = 1.4 })
	ensure("SunRaysEffect", { Intensity = 0.06, Spread = 0.7 })
end

local function buildTower(folder, x, z)
	part(folder, { Name = "TowerPole", Size = Vector3.new(2, 34, 2), Position = Vector3.new(x, 17, z),
		Color = WOOD_DARK, Material = Enum.Material.Wood })
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
	-- the track: a wide wooden boardwalk over the swamp
	part(folder, { Name = "Field", Size = Vector3.new(w, 1, 170), Position = Vector3.new(0, -0.45, 0), -- top 0.05 above the Baseplate (no flicker)
		Color = DECK, Material = Enum.Material.WoodPlanks })
	for z = -84, 84, 4 do -- thin dark seams between the boards
		part(folder, { Name = "Seam", Size = Vector3.new(w, 0.04, 0.35), Position = Vector3.new(0, 0.06, z), Color = WOOD_DARK,
			Material = Enum.Material.Wood })
	end
	for _, side in { -1, 1 } do
		-- invisible walls keep players on the track; a low railing shows the edge
		part(folder, { Name = "Wall", Size = Vector3.new(2, 12, 190), Position = Vector3.new(side * (w / 2 + 1), 6, 5),
			Transparency = 1 })
		part(folder, { Name = "Railing", Size = Vector3.new(0.6, 0.6, 180), Position = Vector3.new(side * (w / 2 + 0.3), 2.4, 0),
			Color = WOOD_DARK, Material = Enum.Material.Wood })
		for z = -88, 88, 8 do
			part(folder, { Name = "RailPost", Size = Vector3.new(0.8, 3, 0.8), Position = Vector3.new(side * (w / 2 + 0.3), 1, z),
				Color = WOOD_DARK, Material = Enum.Material.Wood })
		end
	end
	part(folder, { Name = "BackWall", Size = Vector3.new(w, 12, 2), Position = Vector3.new(0, 6, 96), Transparency = 1 })
	part(folder, { Name = "Lobby", Size = Vector3.new(w, 1, 20), Position = Vector3.new(0, -0.4, 86),
		Color = WOOD_DARK, Material = Enum.Material.WoodPlanks })
	for _, b in workspace:GetChildren() do -- the template's grey Baseplate would show under the water
		if b:IsA("BasePart") and b.Name == "Baseplate" then
			b.Transparency = 1
			for _, tex in b:GetDescendants() do
				if tex:IsA("Texture") or tex:IsA("Decal") then
					tex.Transparency = 1
				end
			end
		end
	end

	local startLine = part(folder, { Name = "StartLine", Size = Vector3.new(w, 0.2, 2),
		Position = Vector3.new(0, 0.1, Course.START_Z), Color = Color3.new(1, 1, 1), Material = Enum.Material.Neon })
	label(startLine, "START", Color3.fromRGB(30, 30, 40))
	local finishLine = part(folder, { Name = "FinishLine", Size = Vector3.new(w, 0.2, 3),
		Position = Vector3.new(0, 0.1, Course.FINISH_Z), Color = Color3.fromRGB(255, 214, 10), Material = Enum.Material.Neon })
	label(finishLine, "FINISH", Color3.fromRGB(30, 30, 40))
	-- the glowing Wi-Fi lane for the real player
	part(folder, { Name = "WiFiLane", Size = Vector3.new(0.4, 0.08, Course.START_Z - Course.FINISH_Z),
		Position = Vector3.new(-3, 0.1, (Course.START_Z + Course.FINISH_Z) / 2), Color = Color3.fromRGB(0, 220, 255),
		Material = Enum.Material.Neon, Transparency = 0.3 })
	part(folder, { Name = "WiFiLane", Size = Vector3.new(0.4, 0.08, Course.START_Z - Course.FINISH_Z),
		Position = Vector3.new(3, 0.1, (Course.START_Z + Course.FINISH_Z) / 2), Color = Color3.fromRGB(0, 220, 255),
		Material = Enum.Material.Neon, Transparency = 0.3 })
	sign(folder, { Name = "LobbySign", Size = Vector3.new(30, 7, 1), Position = Vector3.new(0, 9, 95) },
		"WiFind · Red Light, Green Light\nthe real player is hidden; Wi-Fi decides if they moved",
		WOOD, CREAM)
	buildSwamp(folder, w)

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
	spawn.Color = DECK -- a wooden dock pad, not the default green studs
	spawn.Material = Enum.Material.WoodPlanks
	for _, d in spawn:GetChildren() do
		if d:IsA("Decal") then
			d:Destroy()
		end
	end
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
