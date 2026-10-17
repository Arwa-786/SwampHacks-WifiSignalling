-- GameLoop (Script): Red Light, Green Light, run entirely in Roblox.
--
-- Two kinds of racers share the field:
--   * the REAL player hidden behind the curtain. Their own Roblox avatar (the
--     booth laptop's player, see Config.WIFI_PLAYER_USERID) is driven by Wi-Fi:
--     WifiControl.client turns their keyboard off and walks the avatar while the
--     laptop's verdict is MOVING. Caught if Wi-Fi sees MOVING on red (after the
--     grace period). With no such player in the server, a stand-in avatar runs.
--   * online Roblox players, judged the usual way: Humanoid.MoveDirection.
-- Anyone, including lobby spectators, can press "RED LIGHT!" to end a green early.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage:WaitForChild("WiFindConfig"))
local Shared = require(script.Parent.Shared)
local Course = require(script.Parent.Course)
local Effects = require(script.Parent.Effects)
local WiFi = Shared.WiFi

local course = Course.build()
Course.setLight(course, false)
Players.RespawnTime = 3

local STATE_TEXT = {
	MOVING = "MOVING",
	STILL = "FROZEN",
	EMPTY = "NOT DETECTED",
	UNCALIBRATED = "NOT SCANNED YET",
	SCANNING = "SCANNING ROOM",
	OFFLINE = "WI-FI OFFLINE",
}
local GREY = Color3.fromRGB(120, 120, 130)

local function addLeaderstats(player)
	local stats = Instance.new("Folder")
	stats.Name = "leaderstats"
	local wins = Instance.new("IntValue")
	wins.Name = "Wins"
	wins.Parent = stats
	stats.Parent = player
end
Players.PlayerAdded:Connect(addLeaderstats)
for _, p in Players:GetPlayers() do
	addLeaderstats(p)
end

local function rand(range)
	return range[1] + math.random() * (range[2] - range[1])
end

local greenStartedAt = 0
local function setPhase(phase)
	Shared.Phase.Value = phase
	if phase == "GREEN" then
		greenStartedAt = os.clock()
	end
	if phase == "GREEN" or phase == "RED" or phase == "LOBBY" then
		WiFi.Booth:Fire(phase) -- speaker + LEDs inside the booth follow this
	end
end

-- Red light called by a Roblox player. One shared cooldown so the hidden
-- player isn't spammed, and never in the first 1.5 s of a green.
local redCalledBy = nil
local lastRedCall = -math.huge
Shared.CallRed.OnServerEvent:Connect(function(player)
	local now = os.clock()
	if Shared.Phase.Value ~= "GREEN" or now - greenStartedAt < 1.5 then
		return
	end
	if now - lastRedCall < Config.RED_CALL_COOLDOWN_S then
		return
	end
	lastRedCall = now
	redCalledBy = player.DisplayName
end)

---------------------------------------------------------------- real player

local function makeTag(model)
	local gui = Instance.new("BillboardGui")
	gui.Size = UDim2.fromOffset(260, 60)
	gui.StudsOffset = Vector3.new(0, 3, 0)
	gui.AlwaysOnTop = true
	gui.Adornee = model:FindFirstChild("Head")
	local text = Instance.new("TextLabel")
	text.Size = UDim2.fromScale(1, 1)
	text.BackgroundTransparency = 1
	text.Font = Enum.Font.GothamBlack
	text.TextScaled = true
	text.TextColor3 = Color3.fromRGB(0, 230, 255)
	text.TextStrokeTransparency = 0.3
	text.Parent = gui
	gui.Parent = model
	return text
end

local function spawnRunner()
	local desc = Instance.new("HumanoidDescription")
	local skin = Color3.fromRGB(255, 205, 160)
	desc.HeadColor, desc.LeftArmColor, desc.RightArmColor = skin, skin, skin
	desc.TorsoColor = Color3.fromRGB(0, 200, 220)
	desc.LeftLegColor = Color3.fromRGB(30, 40, 70)
	desc.RightLegColor = Color3.fromRGB(30, 40, 70)

	local ok, model = pcall(Players.CreateHumanoidModelFromDescription, Players, desc, Enum.HumanoidRigType.R15)
	if not ok then
		warn("[WiFind] could not build the Wi-Fi avatar:", model)
		return nil
	end
	model.Name = "WiFiRunner"
	model:PivotTo(CFrame.new(0, 4, Course.START_Z - 2)) -- rigs face -Z, toward the gator
	model.Parent = course.folder

	local humanoid = model:FindFirstChildOfClass("Humanoid")
	humanoid.WalkSpeed = Config.RUNNER_SPEED
	humanoid.DisplayDistanceType = Enum.HumanoidDisplayDistanceType.None
	humanoid.BreakJointsOnDeath = false
	local root = model.HumanoidRootPart
	root:SetNetworkOwner(nil) -- server physics, so nobody's client can push it

	return { model = model, humanoid = humanoid, root = root, status = "RUNNING", tag = makeTag(model) }
end

-- The stand-in avatar tips over, turns grey and stays down; a real player's
-- avatar collapses (in one piece) like any player caught on red.
local function knockOut(runner, text)
	runner.status = "OUT"
	runner.tag.Text = text
	runner.tag.TextColor3 = Color3.fromRGB(255, 59, 72)
	WiFi.Booth:Fire("OUT")
	Effects.caught(runner.root.Position)
	Shared.Fx:FireAllClients("caught", runner.model)
	if runner.player then
		runner.humanoid.Health = 0
		return
	end
	runner.humanoid:Move(Vector3.zero)
	runner.humanoid.PlatformStand = true
	for _, p in runner.model:GetDescendants() do
		if p:IsA("BasePart") then
			p.Color = GREY
		end
	end
	local mass = runner.root.AssemblyMass
	runner.root:ApplyImpulse(Vector3.new(0, 20, 30) * mass)
	runner.root:ApplyAngularImpulse(Vector3.new(40, 0, 0) * mass)
end

-- The player whose avatar Wi-Fi controls: a fixed account, or (0) the first
-- player in the server, which in a Studio test is you.
local function pickWifiPlayer()
	if Config.WIFI_PLAYER_USERID ~= 0 then
		return Players:GetPlayerByUserId(Config.WIFI_PLAYER_USERID)
	end
	return Players:GetPlayers()[1]
end

local function wifiRunnerFor(player)
	local char = player.Character
	local humanoid = char:FindFirstChildOfClass("Humanoid")
	humanoid.WalkSpeed = Config.RUNNER_SPEED
	humanoid.BreakJointsOnDeath = false -- if caught, the avatar collapses in one piece
	char:PivotTo(CFrame.new(0, 4, Course.START_Z - 2)) -- centre lane, facing the gator
	player:SetAttribute("WiFiControlled", true) -- WifiControl.client takes over
	return { player = player, model = char, humanoid = humanoid, root = char.HumanoidRootPart,
		status = "RUNNING", tag = makeTag(char) }
end

---------------------------------------------------------------- one round

local function liveCharacter(player)
	local char = player.Character
	local humanoid = char and char:FindFirstChildOfClass("Humanoid")
	local root = char and char:FindFirstChild("HumanoidRootPart")
	if humanoid and root and humanoid.Health > 0 then
		return humanoid, root
	end
	return nil
end

local function runRound()
	local wifiPlayer = WiFi.State.Value ~= "OFFLINE" and pickWifiPlayer() or nil
	if wifiPlayer and not liveCharacter(wifiPlayer) then
		wifiPlayer = nil
	end
	local racers = {} -- [player] = true while still in the race
	for _, player in Players:GetPlayers() do
		if player ~= wifiPlayer and liveCharacter(player) then
			racers[player] = true
			local x = math.random(5, 26) * (math.random() < 0.5 and -1 or 1) -- centre lane is the runner's
			player.Character:PivotTo(CFrame.new(x, 4, Course.START_Z - 2))
		end
	end
	local runner
	if wifiPlayer then
		runner = wifiRunnerFor(wifiPlayer)
	elseif WiFi.State.Value ~= "OFFLINE" then
		runner = spawnRunner()
	end
	if not runner and next(racers) == nil then
		Shared.Message.Value = "Waiting for players or Wi-Fi..."
		task.wait(3)
		return
	end

	setPhase("COUNTDOWN")
	Course.setLight(course, true)
	for i = 3, 1, -1 do
		Shared.Message.Value = tostring(i)
		task.wait(1)
	end

	local deadline = os.clock() + Config.ROUND_S
	local finishers = 0

	-- Runs every 0.1 s. `judging` = red light and past the grace period.
	-- Returns true when the round is over.
	local function step(judging)
		Shared.TimeLeft.Value = math.max(0, math.ceil(deadline - os.clock()))

		if runner and runner.status == "RUNNING" then
			local sensed = WiFi.State.Value
			-- the avatar mirrors the real person: walks while Wi-Fi says MOVING
			-- (a real player's avatar is walked by their own client, WifiControl)
			if not runner.player then
				runner.humanoid:Move(sensed == "MOVING" and Vector3.new(0, 0, -1) or Vector3.zero)
			end
			runner.tag.Text = "REAL PLAYER · " .. (STATE_TEXT[sensed] or sensed)
			Effects.wifiAura(runner.root.Position, sensed)
			if runner.root.Position.Z <= Course.FINISH_Z then
				runner.status = "WON"
				runner.humanoid:Move(Vector3.zero)
				runner.tag.Text = "REAL PLAYER · MADE IT!"
				finishers += 1
				if runner.player then
					runner.player.leaderstats.Wins.Value += 1
				end
				Shared.Message.Value = "The real player made it past the gator!"
				WiFi.Booth:Fire("WON")
				Effects.confetti(runner.root.Position)
				Shared.Fx:FireAllClients("won", runner.model)
			elseif judging and sensed == "MOVING" then
				knockOut(runner, "CAUGHT BY WI-FI")
				Shared.Message.Value = "Wi-Fi caught the real player moving!"
			end
		end

		for player in racers do
			local humanoid, root = liveCharacter(player)
			if not humanoid or player.Parent == nil then
				racers[player] = nil
			elseif root.Position.Z <= Course.FINISH_Z then
				racers[player] = nil
				finishers += 1
				player.leaderstats.Wins.Value += 1
				Shared.Message.Value = player.DisplayName .. " reached the finish!"
				Effects.confetti(root.Position)
			elseif judging and humanoid.MoveDirection.Magnitude > Config.PLAYER_MOVE_EPS then
				racers[player] = nil
				Effects.caught(root.Position)
				Shared.Fx:FireAllClients("caught", player.Character)
				humanoid.Health = 0
				Shared.Message.Value = player.DisplayName .. " moved on red!"
			end
		end

		-- The hidden real player is the main event: once they are caught or
		-- finish, the round ends for everyone (and the booth stops calling lights).
		if runner and runner.status ~= "RUNNING" then
			return true
		end
		return (runner == nil and next(racers) == nil) or os.clock() >= deadline
	end

	local over = false
	while not over do
		redCalledBy = nil
		setPhase("GREEN")
		Course.setLight(course, false)
		Shared.Message.Value = "Move!"
		local greenEnd = os.clock() + rand(Config.GREEN_RANGE_S)
		while os.clock() < greenEnd and not over do
			if redCalledBy then
				break
			end
			over = step(false)
			task.wait(0.1)
		end
		if over then
			break
		end

		setPhase("RED")
		Course.setLight(course, true)
		Shared.Message.Value = redCalledBy and (redCalledBy .. " called RED LIGHT!") or "Freeze!"
		local redStart = os.clock()
		local redEnd = redStart + rand(Config.RED_RANGE_S)
		while os.clock() < redEnd and not over do
			over = step(os.clock() - redStart >= Config.GRACE_S)
			task.wait(0.1)
		end
	end

	setPhase("RESULTS")
	Course.setLight(course, true)
	-- Only running out of time eliminates the unfinished; a round that ends
	-- because the real player won or was caught just ends.
	if os.clock() >= deadline then
		for player in racers do
			local humanoid = liveCharacter(player)
			if humanoid then
				humanoid.Health = 0
			end
		end
	end
	if runner and runner.status == "RUNNING" then
		knockOut(runner, "TIME'S UP")
	end
	if runner and runner.status == "OUT" then
		Shared.Message.Value = "Round over · Wi-Fi caught the real player"
	else
		Shared.Message.Value = ("Round over · %d reached the finish"):format(finishers)
	end
	task.wait(5)

	if runner and runner.player then
		runner.player:SetAttribute("WiFiControlled", false) -- keyboard back on
	elseif runner then
		runner.model:Destroy()
	end
	for _, player in Players:GetPlayers() do
		player:LoadCharacter() -- everyone back to the lobby
	end
end

while true do
	setPhase("LOBBY")
	Course.setLight(course, false)
	for i = Config.INTERMISSION_S, 1, -1 do
		Shared.Message.Value = ("Next round in %d"):format(i)
		Shared.TimeLeft.Value = i
		task.wait(1)
	end
	runRound()
end
