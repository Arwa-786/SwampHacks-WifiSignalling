-- Fx (LocalScript in StarterPlayerScripts): sounds and camera moments.
--   GREEN    upbeat music (pauses on red)
--   RED      suspense sting + the camera swings to the gator as it turns
--   caught   buzzer + slow grey zoom on whoever moved
--   won      fanfare + the camera swings round to face the winner
-- Sounds are from Roblox's licensed partner libraries (APM Music, Pro Sound
-- Effects), free to use in any experience.

local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local SoundService = game:GetService("SoundService")
local TweenService = game:GetService("TweenService")

local root = ReplicatedStorage:WaitForChild("WiFind")
local Phase = root:WaitForChild("Phase")
local Fx = root:WaitForChild("Fx")
local camera = workspace.CurrentCamera

local function sound(id: number, volume: number, looped: boolean?)
	local s = Instance.new("Sound")
	s.SoundId = "rbxassetid://" .. id
	s.Volume = volume
	s.Looped = looped == true
	s.Parent = SoundService
	return s
end

local music = sound(1835450912, 0.35, true) -- "Crazy Games", APM Music
local redSting = sound(9117320784, 0.9) -- suspense piano sting, Pro Sound Effects
local buzzer = sound(9113085801, 0.8) -- alarm buzzer, Pro Sound Effects
local fanfare = sound(9040233162, 0.9) -- light fun orchestral sting, APM Music

local blur = Instance.new("BlurEffect")
blur.Size = 0
blur.Parent = Lighting
local grey = Instance.new("ColorCorrectionEffect")
grey.Name = "WiFindCaught"
grey.Parent = Lighting

-- One scripted camera shot at a time; afterwards the normal camera returns.
local busy = false
local function shot(target: CFrame, goIn: number, hold: number, onHold: (() -> ())?)
	if busy then
		return
	end
	busy = true
	camera.CameraType = Enum.CameraType.Scriptable
	local back = camera.CFrame
	TweenService:Create(camera, TweenInfo.new(goIn, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), { CFrame = target }):Play()
	task.wait(goIn)
	if onHold then
		onHold()
	end
	task.wait(hold)
	TweenService:Create(camera, TweenInfo.new(0.4, Enum.EasingStyle.Quad), { CFrame = back }):Play()
	task.wait(0.4)
	camera.CameraType = Enum.CameraType.Custom
	busy = false
end

local function gatorHead()
	local course = workspace:FindFirstChild("WiFindCourse")
	local gator = course and course:FindFirstChild("Gator")
	return gator and gator:FindFirstChild("Head")
end

Phase.Changed:Connect(function(phase)
	if phase == "GREEN" then
		if music.IsPaused then
			music:Resume()
		elseif not music.IsPlaying then
			music:Play()
		end
	else
		music:Pause()
	end
	if phase == "RED" then
		redSting:Play()
		local head = gatorHead()
		if head then
			-- over the gator's shoulder... then it turns to face you
			task.spawn(shot, CFrame.lookAt(head.Position + Vector3.new(12, 2, 50), head.Position), 0.45, 0.9)
		end
	end
end)

-- host pause: the music stops with the game
root:WaitForChild("Paused").Changed:Connect(function(paused)
	if paused then
		music:Pause()
	elseif Phase.Value == "GREEN" then
		music:Resume()
	end
end)

Fx.OnClientEvent:Connect(function(kind: string, character: Model?)
	local hrp = character and character:FindFirstChild("HumanoidRootPart")
	if kind == "caught" then
		buzzer:Play()
		if hrp then
			local p = hrp.Position
			task.spawn(shot, CFrame.lookAt(p + Vector3.new(7, 5, 9), p), 1.0, 1.4, function()
				TweenService:Create(blur, TweenInfo.new(0.3), { Size = 10 }):Play()
				TweenService:Create(grey, TweenInfo.new(0.3), { Saturation = -0.9 }):Play()
				task.delay(1.4, function()
					TweenService:Create(blur, TweenInfo.new(0.4), { Size = 0 }):Play()
					TweenService:Create(grey, TweenInfo.new(0.4), { Saturation = 0 }):Play()
				end)
			end)
		end
	elseif kind == "won" then
		fanfare:Play()
		if hrp then
			local p = hrp.Position
			-- from the side, clear of the gator's pedestal just past the finish line
			task.spawn(shot, CFrame.lookAt(p + Vector3.new(14, 6, -6), p + Vector3.new(0, 2, 0)), 0.8, 2.0)
		end
	end
end)

-- joined in the middle of a green light: start the music now
if Phase.Value == "GREEN" then
	music:Play()
end
