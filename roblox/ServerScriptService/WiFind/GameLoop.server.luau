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
--
-- Between rounds Roblox runs the booth, step by step, and tells the laptop each one:
--   RESULTS -> LOBBY ("Hider, step out!") -> SCAN (laptop learns the empty room)
--   -> HIDE ("Next hider, climb in") -> COUNTDOWN -> GREEN/RED ... -> RESULTS
-- Hosts (Config.HOST_USERIDS, the owner, or anyone in Studio) can START NOW,
-- PAUSE, END ROUND and RESCAN from their HUD.

local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

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

---------------------------------------------------------------- clock + host

-- Game time stops while the host has paused, so every timer simply waits.
local pausedAt, pausedTotal = nil, 0
local function now()
	return (pausedAt or os.clock()) - pausedTotal
end
local function wait(dt)
	task.wait(dt)
	while Shared.Paused.Value do
		task.wait(0.1)
	end
end

-- The booth speaker/LEDs (the hidden player's only cue) follow these.
local BOOTH_PHASES = { LOBBY = true, SCAN = true, HIDE = true, COUNTDOWN = true, GREEN = true, RED = true }
local greenStartedAt = 0
local function setPhase(phase)
	Shared.Phase.Value = phase
	if phase == "GREEN" then
		greenStartedAt = now()
	end
	if BOOTH_PHASES[phase] then
		WiFi.Booth:Fire(phase)
	end
end

local function isHost(player)
	return RunService:IsStudio()
		or player.UserId == game.CreatorId
		or table.find(Config.HOST_USERIDS, player.UserId) ~= nil
end
local function markHost(player)
	player:SetAttribute("WiFindHost", isHost(player)) -- the HUD shows host buttons
end
Players.PlayerAdded:Connect(markHost)
for _, p in Players:GetPlayers() do
	markHost(p)
end

local request = { start = false, finish = false } -- set by host buttons, read by the loop
local current = { runner = nil } -- the running round's Wi-Fi runner, for pause
Shared.Host.OnServerEvent:Connect(function(player, cmd)
	if not isHost(player) then
		return
	end
	local phase = Shared.Phase.Value
	if cmd == "start" and (phase == "LOBBY" or phase == "HIDE") then
		request.start = true
	elseif cmd == "end" and (phase == "COUNTDOWN" or phase == "GREEN" or phase == "RED") then
		request.finish = true
		if Shared.Paused.Value then -- un-pause so the loop can wrap up
			pausedTotal += os.clock() - pausedAt
			pausedAt = nil
			Shared.Paused.Value = false
		end
	elseif cmd == "rescan" and (phase == "LOBBY" or phase == "HIDE") then
		WiFi.Booth:Fire("SCAN") -- the laptop announces it and scans now
	elseif cmd == "pause" and (phase == "GREEN" or phase == "RED") then
		local paused = not Shared.Paused.Value
		if paused then
			pausedAt = os.clock()
			local r = current.runner
			if r and not r.player and r.status == "RUNNING" then
				r.humanoid:Move(Vector3.zero)
			end
			Shared.Message.Value = "Paused by the host"
			WiFi.Booth:Fire("PAUSED")
		else
			pausedTotal += os.clock() - pausedAt
			pausedAt = nil
			Shared.Message.Value = phase == "RED" and "Freeze!" or "Move!"
			WiFi.Booth:Fire(phase) -- say the light again so the hider knows
		end
		Shared.Paused.Value = paused
	end
end)

-- Red light called by a Roblox player. One shared cooldown so the hidden
-- player isn't spammed, and never in the first 1.5 s of a green.
local redCalledBy = nil
local lastRedCall = -math.huge
Shared.CallRed.OnServerEvent:Connect(function(player)
	local t = now()
	if Shared.Phase.Value ~= "GREEN" or Shared.Paused.Value or t - greenStartedAt < 1.5 then
		return
	end
	if t - lastRedCall < Config.RED_CALL_COOLDOWN_S then
		return
	end
	lastRedCall = t
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

local function clockText(seconds)
	seconds = math.max(0, math.floor(seconds))
	return ("%d:%02d"):format(seconds // 60, seconds % 60)
end

-- Fills the results card everyone sees after a round.
local function showResults(result)
	Shared.Results.Value = HttpService:JSONEncode(result)
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
	current.runner = runner
	request.finish = false

	setPhase("COUNTDOWN")
	Course.setLight(course, true)
	for i = 3, 1, -1 do
		Shared.Message.Value = tostring(i)
		wait(1)
	end

	local startedAt = now()
	local deadline = startedAt + Config.ROUND_S
	local finishers, caughtOnline = {}, {}
	local redLights, redStart = 0, nil
	local caughtAfterRed = nil -- seconds after "red light" the Wi-Fi caught the hider
	local endedAt = nil
	local playerMovingSince = {}

	-- Runs every 0.1 s. `judging` = red light and past the grace period.
	-- Returns true when the round is over.
	local function step(judging)
		Shared.TimeLeft.Value = math.max(0, math.ceil(deadline - now()))

		if runner and runner.status == "RUNNING" then
			local sensed = WiFi.State.Value
			-- the avatar mirrors the real person: walks while Wi-Fi says MOVING
			-- (a real player's avatar is walked by their own client, WifiControl)
			if not runner.player then
				runner.humanoid:Move(sensed == "MOVING" and Vector3.new(0, 0, -1) or Vector3.zero)
			end
			runner.tag.Text = "REAL PLAYER · " .. (STATE_TEXT[sensed] or sensed)
			Effects.wifiAura(runner.root.Position, sensed)
			-- a catch needs steady movement, not one twitch (Config.CATCH_HOLD_S)
			runner.movingSince = sensed == "MOVING" and (runner.movingSince or now()) or nil
			if runner.root.Position.Z <= Course.FINISH_Z then
				runner.status = "WON"
				endedAt = now()
				runner.humanoid:Move(Vector3.zero)
				runner.tag.Text = "REAL PLAYER · MADE IT!"
				table.insert(finishers, "the hidden player")
				if runner.player then
					runner.player.leaderstats.Wins.Value += 1
				end
				Shared.Message.Value = "The real player made it past the gator!"
				WiFi.Booth:Fire("WON")
				Effects.confetti(runner.root.Position)
				Shared.Fx:FireAllClients("won", runner.model)
			elseif judging and runner.movingSince and now() - runner.movingSince >= Config.CATCH_HOLD_S then
				endedAt = now()
				caughtAfterRed = now() - redStart
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
				table.insert(finishers, player.DisplayName)
				player.leaderstats.Wins.Value += 1
				Shared.Message.Value = player.DisplayName .. " reached the finish!"
				Effects.confetti(root.Position)
			else
				local moving = humanoid.MoveDirection.Magnitude > Config.PLAYER_MOVE_EPS
				playerMovingSince[player] = moving and (playerMovingSince[player] or now()) or nil
				if judging and moving and now() - playerMovingSince[player] >= Config.PLAYER_CATCH_HOLD_S then
					racers[player] = nil
					table.insert(caughtOnline, player.DisplayName)
					Effects.caught(root.Position)
					Shared.Fx:FireAllClients("caught", player.Character)
					humanoid.Health = 0
					Shared.Message.Value = player.DisplayName .. " moved on red!"
				end
			end
		end

		-- The hidden real player is the main event: once they are caught or
		-- finish, the round ends for everyone (and the booth stops calling lights).
		if runner and runner.status ~= "RUNNING" then
			return true
		end
		if request.finish then
			return true
		end
		return (runner == nil and next(racers) == nil) or now() >= deadline
	end

	local over = false
	while not over do
		redCalledBy = nil
		setPhase("GREEN")
		Course.setLight(course, false)
		Shared.Message.Value = "Move!"
		local greenEnd = now() + rand(Config.GREEN_RANGE_S)
		while now() < greenEnd and not over do
			if redCalledBy then
				break
			end
			over = step(false)
			wait(0.1)
		end
		if over then
			break
		end

		setPhase("RED")
		redLights += 1
		Course.setLight(course, true)
		Shared.Message.Value = redCalledBy and (redCalledBy .. " called RED LIGHT!") or "Freeze!"
		redStart = now()
		local redEnd = redStart + rand(Config.RED_RANGE_S)
		while now() < redEnd and not over do
			over = step(now() - redStart >= Config.GRACE_S)
			wait(0.1)
		end
	end
	current.runner = nil
	endedAt = endedAt or now()

	-- Only running out of time eliminates the unfinished; a round that ends
	-- because the real player won or was caught (or the host ended it) just ends.
	local timeUp = now() >= deadline and not request.finish
	if timeUp then
		for player in racers do
			local humanoid = liveCharacter(player)
			if humanoid then
				humanoid.Health = 0
			end
		end
	end
	if runner and runner.status == "RUNNING" and timeUp then
		knockOut(runner, "TIME'S UP")
	end

	-- The results card: what happened, in plain words.
	local survived = clockText(endedAt - startedAt)
	local result = { rows = {}, caught = false }
	if request.finish then
		result.title, result.sub = "ROUND ENDED", "The host ended this round early"
	elseif runner and runner.status == "WON" then
		result.title, result.sub = "THE HIDER MADE IT!", "The Wi-Fi never caught them moving on red"
	elseif runner and caughtAfterRed then
		result.title, result.caught = "CAUGHT BY WI-FI", true
		result.sub = ("They moved %.1f s after red light, and the Wi-Fi felt it"):format(caughtAfterRed)
	elseif runner then
		result.title, result.sub = "TIME'S UP", "The hider didn't reach the gator in time"
	else
		result.title, result.sub = "ROUND OVER", "Online players only this time: the Wi-Fi is offline"
	end
	if runner then
		local survivedReds = math.max(0, redLights - (caughtAfterRed and 1 or 0))
		table.insert(result.rows, { "Hider survived", survived })
		table.insert(result.rows, { "Red lights survived", tostring(survivedReds) })
	end
	if #finishers > 0 then
		table.insert(result.rows, { "Made it", table.concat(finishers, ", ") })
	end
	if #caughtOnline > 0 then
		table.insert(result.rows, { "Moved on red", table.concat(caughtOnline, ", ") })
	end
	Shared.GatorLine.Value = "" -- the bridge fills in the AI gator's line when the laptop has it
	showResults(result)
	Shared.Message.Value = result.title
	setPhase("RESULTS")
	Course.setLight(course, true)
	request.finish = false
	task.wait(Config.RESULTS_S)

	if runner and runner.player then
		runner.player:SetAttribute("WiFiControlled", false) -- keyboard back on
	elseif runner then
		runner.model:Destroy()
	end
	for _, player in Players:GetPlayers() do
		player:LoadCharacter() -- everyone back to the lobby
	end
end

---------------------------------------------------------------- between rounds

-- Counts down `seconds`, showing `text`; returns early when a host presses
-- START NOW (if `skippable`) or when `doneEarly()` says so.
local function countdown(seconds, text, skippable, doneEarly)
	local stop = os.clock() + seconds
	while os.clock() < stop do
		if skippable and request.start then
			return "start"
		end
		if doneEarly and doneEarly() then
			return "done"
		end
		Shared.Message.Value = text
		Shared.TimeLeft.Value = math.ceil(stop - os.clock())
		task.wait(0.2)
	end
	return "timeout"
end

-- LOBBY -> SCAN -> HIDE. The laptop scans the empty room only when Roblox
-- says SCAN, so a scan can never start in the middle of a round. A host's
-- START NOW skips whatever is left (skipping the scan keeps the previous one).
local function betweenRounds()
	request.start = false
	Shared.Results.Value = ""
	Course.setLight(course, false)
	if WiFi.State.Value == "OFFLINE" then
		-- also the first seconds after the server starts, before the bridge's
		-- first answer: if the Wi-Fi comes online, run the full booth sequence
		setPhase("LOBBY")
		local how = countdown(Config.LOBBY_S, "Wi-Fi offline · online players only", true, function()
			return WiFi.State.Value ~= "OFFLINE"
		end)
		if how ~= "done" then
			request.start = false
			return
		end
	end

	setPhase("LOBBY")
	if countdown(Config.STEP_OUT_S, "Hider, step out of the booth!", true) == "start" then
		request.start = false
		return
	end

	setPhase("SCAN") -- the laptop says "listening to the empty swamp" and scans
	local sawScan = false
	countdown(Config.SCAN_TIMEOUT_S, "Listening to the empty swamp...", false, function()
		local st = WiFi.State.Value
		sawScan = sawScan or st == "SCANNING"
		return (sawScan and st ~= "SCANNING") or st == "OFFLINE"
	end)

	setPhase("HIDE")
	countdown(Config.HIDE_S, "Next hider, climb in and get comfy!", true)
	request.start = false
end

while true do
	betweenRounds()
	runRound()
end
