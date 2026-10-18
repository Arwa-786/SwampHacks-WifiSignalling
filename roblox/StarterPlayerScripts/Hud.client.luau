-- Hud (LocalScript in StarterPlayerScripts): builds the on-screen UI in code.
--   top:          GREEN LIGHT / RED LIGHT banner, message line, round timer
--   bottom-left:  "Wi-Fi vision" card: what the sensors see of the real player
--   bottom-right: RED LIGHT! button for online players and spectators
--   centre:       between rounds, a card explaining the game + what's happening
--                 (step out / scanning / hide); after a round, the results card
--   right:        host buttons under the timer (START NOW, PAUSE, END ROUND, RESCAN), hosts only

local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")
local Lighting = game:GetService("Lighting")

local Config = require(ReplicatedStorage:WaitForChild("WiFindConfig"))
local root = ReplicatedStorage:WaitForChild("WiFind")
local Phase = root:WaitForChild("Phase")
local Message = root:WaitForChild("Message")
local TimeLeft = root:WaitForChild("TimeLeft")
local CallRed = root:WaitForChild("CallRed")
local Paused = root:WaitForChild("Paused")
local Results = root:WaitForChild("Results")
local GatorLine = root:WaitForChild("GatorLine")
local Host = root:WaitForChild("Host")
local wifi = root:WaitForChild("WiFi")
local WState = wifi:WaitForChild("State")
local WMotion = wifi:WaitForChild("Motion")
local WReceivers = wifi:WaitForChild("Receivers")

local GREEN = Color3.fromRGB(46, 230, 120)
local RED = Color3.fromRGB(255, 59, 72)
local INK = Color3.fromRGB(16, 18, 28)
local PANEL = Color3.fromRGB(22, 25, 38)
local MUTED = Color3.fromRGB(150, 156, 180)
local CYAN = Color3.fromRGB(0, 220, 255)

local function new(className, props, parent)
	local inst = Instance.new(className)
	for k, v in props do
		inst[k] = v
	end
	inst.Parent = parent
	return inst
end
local function round(inst, px)
	new("UICorner", { CornerRadius = UDim.new(0, px) }, inst)
end
local function text(props, parent)
	props.BackgroundTransparency = 1
	props.Font = props.Font or Enum.Font.GothamBold
	props.TextColor3 = props.TextColor3 or Color3.new(1, 1, 1)
	return new("TextLabel", props, parent)
end

local gui = new("ScreenGui", { Name = "WiFindHud", ResetOnSpawn = false, IgnoreGuiInset = true },
	Players.LocalPlayer:WaitForChild("PlayerGui"))

-- red flash over the whole screen when the light turns red
local flash = new("Frame", { Size = UDim2.fromScale(1, 1), BackgroundColor3 = RED, BackgroundTransparency = 1,
	BorderSizePixel = 0, ZIndex = 0 }, gui)
local tint = new("ColorCorrectionEffect", { Name = "WiFindTint" }, Lighting)

---------------------------------------------------------------- top banner
local banner = new("Frame", { AnchorPoint = Vector2.new(0.5, 0), Position = UDim2.new(0.5, 0, 0, 56),
	Size = UDim2.fromOffset(380, 70), BackgroundColor3 = GREEN }, gui)
round(banner, 35)
new("UIStroke", { Thickness = 3, Color = Color3.new(1, 1, 1), Transparency = 0.6 }, banner)
local bannerText = text({ Size = UDim2.fromScale(1, 1), Font = Enum.Font.GothamBlack, TextScaled = true,
	TextColor3 = INK, Text = "LOBBY" }, banner)
new("UIPadding", { PaddingTop = UDim.new(0, 12), PaddingBottom = UDim.new(0, 12) }, bannerText)

local message = text({ AnchorPoint = Vector2.new(0.5, 0), Position = UDim2.new(0.5, 0, 0, 134),
	Size = UDim2.fromOffset(700, 34), TextScaled = true, Text = "", TextStrokeTransparency = 0.5 }, gui)

local timer = new("Frame", { AnchorPoint = Vector2.new(1, 0), Position = UDim2.new(1, -24, 0, 56),
	Size = UDim2.fromOffset(110, 70), BackgroundColor3 = PANEL, BackgroundTransparency = 0.1 }, gui)
round(timer, 16)
text({ Size = UDim2.new(1, 0, 0, 22), Position = UDim2.fromOffset(0, 6), Text = "TIME", TextColor3 = MUTED,
	TextSize = 14 }, timer)
local timerText = text({ Size = UDim2.new(1, 0, 0, 36), Position = UDim2.fromOffset(0, 26),
	Font = Enum.Font.GothamBlack, TextSize = 32, Text = "--" }, timer)

---------------------------------------------------------------- Wi-Fi card
local card = new("Frame", { AnchorPoint = Vector2.new(0, 1), Position = UDim2.new(0, 24, 1, -24),
	Size = UDim2.fromOffset(300, 150), BackgroundColor3 = PANEL, BackgroundTransparency = 0.08 }, gui)
round(card, 18)
new("UIStroke", { Thickness = 2, Color = CYAN, Transparency = 0.5 }, card)
text({ Position = UDim2.fromOffset(18, 12), Size = UDim2.new(1, -36, 0, 18), Text = "WI-FI VISION · REAL PLAYER",
	TextColor3 = CYAN, TextSize = 13, TextXAlignment = Enum.TextXAlignment.Left }, card)
local stateText = text({ Position = UDim2.fromOffset(18, 34), Size = UDim2.new(1, -36, 0, 40),
	Font = Enum.Font.GothamBlack, TextScaled = true, Text = "--", TextXAlignment = Enum.TextXAlignment.Left }, card)

-- motion meter: the white tick is the calibrated threshold (motion = 1.0)
local METER_MAX = 3
local meter = new("Frame", { Position = UDim2.fromOffset(18, 86), Size = UDim2.new(1, -36, 0, 12),
	BackgroundColor3 = Color3.fromRGB(45, 50, 70) }, card)
round(meter, 6)
local fill = new("Frame", { Size = UDim2.fromScale(0, 1), BackgroundColor3 = GREEN }, meter)
round(fill, 6)
new("Frame", { Position = UDim2.new(1 / METER_MAX, -1, 0, -3), Size = UDim2.new(0, 2, 1, 6),
	BackgroundColor3 = Color3.new(1, 1, 1), BorderSizePixel = 0 }, meter)

local dotsRow = new("Frame", { Position = UDim2.fromOffset(18, 110), Size = UDim2.new(1, -36, 0, 24),
	BackgroundTransparency = 1 }, card)
new("UIListLayout", { FillDirection = Enum.FillDirection.Horizontal, Padding = UDim.new(0, 8),
	VerticalAlignment = Enum.VerticalAlignment.Center }, dotsRow)

---------------------------------------------------------------- red button
local button = new("TextButton", { AnchorPoint = Vector2.new(1, 1), Position = UDim2.new(1, -24, 1, -24),
	Size = UDim2.fromOffset(200, 84), BackgroundColor3 = RED, Font = Enum.Font.GothamBlack, TextScaled = true,
	TextColor3 = Color3.new(1, 1, 1), Text = "RED LIGHT!", AutoButtonColor = true }, gui)
round(button, 20)
new("UIPadding", { PaddingTop = UDim.new(0, 18), PaddingBottom = UDim.new(0, 18) }, button)
new("UIStroke", { Thickness = 3, Color = Color3.new(1, 1, 1), Transparency = 0.4,
	ApplyStrokeMode = Enum.ApplyStrokeMode.Border }, button)

local readyAt = 0
button.Activated:Connect(function()
	if os.clock() < readyAt or Phase.Value ~= "GREEN" then
		return
	end
	readyAt = os.clock() + Config.RED_CALL_COOLDOWN_S
	CallRed:FireServer()
end)

---------------------------------------------------------------- centre card

-- One card in the middle of the screen. Between rounds it explains the game
-- (people joining mid-way need to know a real person is hiding); after a round
-- it shows the results.
local cardC = new("Frame", { AnchorPoint = Vector2.new(0.5, 0.5), Position = UDim2.fromScale(0.5, 0.5),
	Size = UDim2.fromOffset(540, 0), AutomaticSize = Enum.AutomaticSize.Y, BackgroundColor3 = PANEL,
	BackgroundTransparency = 0.04, Visible = false }, gui)
round(cardC, 24)
new("UIStroke", { Thickness = 3, Color = Color3.new(1, 1, 1), Transparency = 0.75 }, cardC)
new("UIPadding", { PaddingLeft = UDim.new(0, 28), PaddingRight = UDim.new(0, 28), PaddingTop = UDim.new(0, 22),
	PaddingBottom = UDim.new(0, 22) }, cardC)
new("UIListLayout", { Padding = UDim.new(0, 10), SortOrder = Enum.SortOrder.LayoutOrder }, cardC)
local cTitle = text({ Size = UDim2.new(1, 0, 0, 46), Font = Enum.Font.GothamBlack, TextScaled = true, LayoutOrder = 1 }, cardC)
local cSub = text({ Size = UDim2.new(1, 0, 0, 44), TextSize = 19, TextWrapped = true, TextColor3 = MUTED,
	LayoutOrder = 2 }, cardC)
local cRows = new("Frame", { Size = UDim2.new(1, 0, 0, 0), AutomaticSize = Enum.AutomaticSize.Y,
	BackgroundTransparency = 1, LayoutOrder = 3 }, cardC)
new("UIListLayout", { Padding = UDim.new(0, 6), SortOrder = Enum.SortOrder.LayoutOrder }, cRows)
local cGator = text({ Size = UDim2.new(1, 0, 0, 0), AutomaticSize = Enum.AutomaticSize.Y, TextSize = 20,
	TextWrapped = true, TextColor3 = Color3.fromRGB(150, 230, 120), Font = Enum.Font.GothamMedium, LayoutOrder = 4 }, cardC)
local cStatus = text({ Size = UDim2.new(1, 0, 0, 34), Font = Enum.Font.GothamBlack, TextScaled = true,
	TextColor3 = CYAN, LayoutOrder = 5 }, cardC)

local function setRows(rows)
	for _, r in cRows:GetChildren() do
		if r:IsA("Frame") then
			r:Destroy()
		end
	end
	for i, row in rows do
		local line = new("Frame", { Size = UDim2.new(1, 0, 0, 30), BackgroundColor3 = Color3.fromRGB(36, 40, 58),
			LayoutOrder = i }, cRows)
		round(line, 10)
		text({ Position = UDim2.fromOffset(14, 0), Size = UDim2.new(0.5, -14, 1, 0), Text = row[1], TextSize = 17,
			TextColor3 = MUTED, TextXAlignment = Enum.TextXAlignment.Left }, line)
		text({ Position = UDim2.new(0.5, 0, 0, 0), Size = UDim2.new(0.5, -14, 1, 0), Text = row[2], TextSize = 18,
			Font = Enum.Font.GothamBlack, TextXAlignment = Enum.TextXAlignment.Right, TextTruncate = Enum.TextTruncate.AtEnd }, line)
	end
end

local INTRO_ROWS = {
	{ "Behind a curtain at the booth", "a real person" },
	{ "Watching them", "only Wi-Fi, no camera" },
	{ "The cyan avatar", "is them" },
	{ "Your job", "call RED LIGHT!" },
}
local BETWEEN = { LOBBY = true, SCAN = true, HIDE = true }

local function updateCard()
	local phase = Phase.Value
	if phase == "RESULTS" and Results.Value ~= "" then
		local ok, r = pcall(HttpService.JSONDecode, HttpService, Results.Value)
		if not ok then
			return
		end
		cardC.Visible = true
		cTitle.Text, cTitle.TextColor3 = r.title, r.caught and RED or GREEN
		cSub.Text = r.sub
		setRows(r.rows or {})
		cGator.Text = GatorLine.Value ~= "" and ("Gator: \"" .. GatorLine.Value .. "\"") or ""
		cStatus.Text = "Next round soon"
	elseif BETWEEN[phase] then
		cardC.Visible = true
		cTitle.Text, cTitle.TextColor3 = "WIFIND: RED LIGHT, GREEN LIGHT", Color3.new(1, 1, 1)
		cSub.Text = "Can Wi-Fi signals tell if someone hiding behind a curtain moves? Try to catch them."
		setRows(INTRO_ROWS)
		cGator.Text = ""
		cStatus.Text = Message.Value
	else
		cardC.Visible = false
	end
	message.Visible = not cardC.Visible -- the card already says it
end

---------------------------------------------------------------- host buttons

local hostPanel = new("Frame", { AnchorPoint = Vector2.new(1, 0), Position = UDim2.new(1, -24, 0, 140),
	Size = UDim2.fromOffset(150, 0),
	AutomaticSize = Enum.AutomaticSize.Y, BackgroundTransparency = 1, Visible = false }, gui)
new("UIListLayout", { Padding = UDim.new(0, 8), SortOrder = Enum.SortOrder.LayoutOrder }, hostPanel)
text({ Size = UDim2.new(1, 0, 0, 16), Text = "HOST", TextSize = 13, TextColor3 = MUTED, LayoutOrder = 0,
	TextXAlignment = Enum.TextXAlignment.Left }, hostPanel)
local hostButtons = {}
local function hostButton(cmd, label, color, order)
	local b = new("TextButton", { Size = UDim2.new(1, 0, 0, 40), BackgroundColor3 = color, Font = Enum.Font.GothamBlack,
		TextSize = 16, TextColor3 = Color3.new(1, 1, 1), Text = label, LayoutOrder = order, AutoButtonColor = true }, hostPanel)
	round(b, 12)
	b.Activated:Connect(function()
		Host:FireServer(cmd)
	end)
	hostButtons[cmd] = b
	return b
end
hostButton("start", "START NOW", Color3.fromRGB(40, 170, 90), 1)
hostButton("pause", "PAUSE", Color3.fromRGB(90, 96, 130), 2)
hostButton("end", "END ROUND", Color3.fromRGB(200, 70, 60), 3)
hostButton("rescan", "RESCAN ROOM", Color3.fromRGB(40, 120, 170), 4)

-- which host buttons make sense right now (the server checks again)
local function updateHost()
	hostPanel.Visible = Players.LocalPlayer:GetAttribute("WiFindHost") == true
	local phase = Phase.Value
	local usable = {
		start = phase == "LOBBY" or phase == "HIDE",
		pause = phase == "GREEN" or phase == "RED",
		["end"] = phase == "COUNTDOWN" or phase == "GREEN" or phase == "RED",
		rescan = phase == "LOBBY" or phase == "HIDE",
	}
	for cmd, b in hostButtons do
		b.BackgroundTransparency = usable[cmd] and 0 or 0.65
		b.TextTransparency = usable[cmd] and 0 or 0.4
		b.Active = usable[cmd]
	end
	hostButtons.pause.Text = Paused.Value and "RESUME" or "PAUSE"
end
Players.LocalPlayer:GetAttributeChangedSignal("WiFindHost"):Connect(updateHost)

---------------------------------------------------------------- updates
local STATE_LOOK = {
	MOVING = { "MOVING", RED },
	STILL = { "FROZEN", GREEN },
	EMPTY = { "NOBODY THERE", MUTED },
	UNCALIBRATED = { "NOT SCANNED YET", MUTED },
	SCANNING = { "SCANNING ROOM...", CYAN },
	OFFLINE = { "WI-FI OFFLINE", MUTED },
}
local BANNER = {
	LOBBY = { "STEP OUT", PANEL, Color3.new(1, 1, 1) },
	SCAN = { "SCANNING", CYAN, INK },
	HIDE = { "HIDE!", Color3.fromRGB(255, 214, 10), INK },
	PAUSED = { "PAUSED", Color3.fromRGB(90, 96, 130), Color3.new(1, 1, 1) },
	COUNTDOWN = { "GET READY", Color3.fromRGB(255, 214, 10), INK },
	GREEN = { "GREEN LIGHT", GREEN, INK },
	RED = { "RED LIGHT", RED, Color3.new(1, 1, 1) },
	RESULTS = { "ROUND OVER", PANEL, Color3.new(1, 1, 1) },
}

local function onPhase()
	updateCard()
	updateHost()
	local look = BANNER[Paused.Value and "PAUSED" or Phase.Value] or BANNER.LOBBY
	bannerText.Text, banner.BackgroundColor3, bannerText.TextColor3 = look[1], look[2], look[3]
	banner.Size = UDim2.fromOffset(420, 78) -- pop, then settle
	TweenService:Create(banner, TweenInfo.new(0.3, Enum.EasingStyle.Back), { Size = UDim2.fromOffset(380, 70) }):Play()
	local isRed = Phase.Value == "RED"
	if isRed then
		flash.BackgroundTransparency = 0.55
		TweenService:Create(flash, TweenInfo.new(0.8), { BackgroundTransparency = 1 }):Play()
	end
	TweenService:Create(tint, TweenInfo.new(0.4), {
		TintColor = isRed and Color3.fromRGB(255, 200, 200) or Color3.new(1, 1, 1),
		Saturation = isRed and -0.2 or 0,
	}):Play()
end

local function onWifi()
	local look = STATE_LOOK[WState.Value] or { WState.Value, MUTED }
	stateText.Text, stateText.TextColor3 = look[1], look[2]
	local m = math.clamp(WMotion.Value / METER_MAX, 0, 1)
	TweenService:Create(fill, TweenInfo.new(0.2), { Size = UDim2.fromScale(m, 1),
		BackgroundColor3 = WMotion.Value >= 1 and RED or GREEN }):Play()

	for _, d in dotsRow:GetChildren() do
		if d:IsA("Frame") then
			d:Destroy()
		end
	end
	for i, s in string.split(WReceivers.Value, ",") do
		if s ~= "" then
			local chip = new("Frame", { Size = UDim2.fromOffset(78, 24), LayoutOrder = i,
				BackgroundColor3 = s == "MOVING" and RED or Color3.fromRGB(45, 50, 70) }, dotsRow)
			round(chip, 12)
			text({ Size = UDim2.fromScale(1, 1), Text = "RX" .. i, TextSize = 13 }, chip)
		end
	end
end

Phase.Changed:Connect(onPhase)
Message.Changed:Connect(function()
	message.Text = Message.Value
	if BETWEEN[Phase.Value] then
		cStatus.Text = Message.Value
	end
end)
Paused.Changed:Connect(onPhase)
Results.Changed:Connect(updateCard)
GatorLine.Changed:Connect(updateCard)
TimeLeft.Changed:Connect(function()
	timerText.Text = tostring(TimeLeft.Value)
end)
WState.Changed:Connect(onWifi)
WMotion.Changed:Connect(onWifi)
WReceivers.Changed:Connect(onWifi)
onPhase()
onWifi()
message.Text = Message.Value

-- button shows its cooldown
while true do
	local left = readyAt - os.clock()
	local usable = left <= 0 and Phase.Value == "GREEN"
	button.Text = left > 0 and ("WAIT %d"):format(math.ceil(left)) or "RED LIGHT!"
	button.BackgroundTransparency = usable and 0 or 0.5
	task.wait(0.2)
end
