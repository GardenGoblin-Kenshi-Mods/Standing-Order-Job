using System.Numerics;
using Meitou.Data.Gameplay.Ai;
using Meitou.Data.Save;
using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Movement;
using Meitou.Simulation.Player;
using Meitou.Simulation.Production;
using StandingOrderJob.Tests.Harness;
using Xunit;

namespace StandingOrderJob.Tests.Behavioral;

/// <summary>Layer 2 behavioral scenarios verifying Stand Here decision logic, priority blocking, cleanup, and return distance gating (<c>B-02</c>.. <c>B-08</c>).</summary>
public sealed class StandHereScenarioTests
{
    const TaskType LowerStubJobType = (TaskType)(SimulationExtensions.FirstModCode + 99);
    static readonly Action<string> NoOpLog = static _ => { };

    /// <summary>Minimal lower-priority job used to verify when Stand Here blocks vs yields to jobs listed below it.</summary>
    sealed class StubLowerJobSystem : ITickSystem, IChooserOwner, IJobSource
    {
        public bool ShouldRun { get; set; } = true;
        public int RunCount { get; private set; }

        public void Register(ChooserRegistry chooser) => chooser.Jobs(LowerStubJobType, this);

        public DecisionCheck CanRun(World world, int slot, int jobIndex)
        {
            if (!ShouldRun)
                return DecisionCheck.No("disabled");

            var cold = world.Characters.Cold(slot);
            bool alreadyRunning = cold?.Running is { Source: TaskSource.Job, Row: LowerStubJobType } run && run.Job == jobIndex;
            return alreadyRunning ? DecisionCheck.Runs : DecisionCheck.Run;
        }

        public void Start(World world, int slot, int jobIndex)
        {
            RunCount++;
            TaskStart.Start(world, slot, new TaskRequest(CharacterTask.Idle, LowerStubJobType, TaskSource.Job)
            {
                Job = jobIndex,
            });
        }
    }

    [Fact]
    public void UnshiftedStandHereMovesCharacterWithoutAlteringExistingJobOrPoint()
    {
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var system);
        var actor = testWorld.SpawnPlayer("Runner", new Vector3(10f, 0f, 10f));

        // First assign a permanent job at (10, 0, 10) with Jobs temporarily toggled off so one-shot moves cleanly finish.
        var initialPost = new Vector3(10f, 0f, 10f);
        testWorld.World.Commands.Enqueue(new StandHereCommand([actor.Id], initialPost, Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(5);
        actor.Cold.Orders &= ~CharacterOrders.Jobs;

        var oneShotTarget = new Vector3(45f, 0f, 10f);
        testWorld.World.Commands.Enqueue(new StandHereCommand([actor.Id], oneShotTarget, Permanent: false)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(60);

        Assert.True(StandHereTypes.IsNearPoint(testWorld.GetPosition(actor.Id), oneShotTarget));
        Assert.True(system.TryGetStandPoint(actor.Cold, out var storedPoint));
        Assert.Equal(initialPost, storedPoint);
        Assert.Single(actor.Cold.Jobs);
    }

    [Fact]
    public void ShiftedStandHereFiltersMixedRecipientsUpdatesInPlaceAndWaitsUntilConscious()
    {
        var lowerStub = new StubLowerJobSystem();
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var system, lowerStub);

        var consciousPlayer = testWorld.SpawnPlayer("Conscious", new Vector3(10f, 0f, 10f));
        var koPlayer = testWorld.SpawnPlayer("KnockedOut", new Vector3(15f, 0f, 25f), isIncapacitated: true);
        var deadPlayer = testWorld.SpawnPlayer("Dead", new Vector3(20f, 0f, 40f), isDead: true);
        var animalPlayer = testWorld.SpawnPlayer("Bonedog", new Vector3(25f, 0f, 40f), isAnimal: true);
        var npc = testWorld.SpawnNpc("Stranger", new Vector3(30f, 0f, 40f));

        // Give Conscious a lower job first so we can confirm Stand Here index 1 updates in place when reassigned.
        consciousPlayer.Cold.Jobs.Add(new PermaJob(LowerStubJobType, null, Hand.Null));
        var postA = new Vector3(40f, 0f, 10f);
        testWorld.World.Commands.Enqueue(new StandHereCommand(
            [consciousPlayer.Id, koPlayer.Id, deadPlayer.Id, animalPlayer.Id, npc.Id],
            postA,
            Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(5);

        Assert.Equal(1, PermaJobs.IndexOf(consciousPlayer.Cold, StandHereTypes.JobType));
        Assert.Equal(0, PermaJobs.IndexOf(koPlayer.Cold, StandHereTypes.JobType));
        Assert.Equal(-1, PermaJobs.IndexOf(deadPlayer.Cold, StandHereTypes.JobType));
        Assert.Equal(-1, PermaJobs.IndexOf(animalPlayer.Cold, StandHereTypes.JobType));
        Assert.Equal(-1, PermaJobs.IndexOf(npc.Cold, StandHereTypes.JobType));

        // KO player kept job + point, but stays put until conscious.
        Assert.True(system.TryGetStandPoint(koPlayer.Cold, out var koStoredPoint));
        Assert.Equal(postA, koStoredPoint);
        Assert.Equal(new Vector3(15f, 0f, 25f), testWorld.GetPosition(koPlayer.Id));

        // Reassign Stand Here on Conscious to postB (50,0,10); existing row updates in place at index 1 while koPlayer keeps postA.
        var postB = new Vector3(50f, 0f, 10f);
        testWorld.World.Commands.Enqueue(new StandHereCommand([consciousPlayer.Id], postB, Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        lowerStub.ShouldRun = false;
        testWorld.RunTicks(60);

        Assert.Equal(2, consciousPlayer.Cold.Jobs.Count);
        Assert.Equal(1, PermaJobs.IndexOf(consciousPlayer.Cold, StandHereTypes.JobType));
        Assert.True(system.TryGetStandPoint(consciousPlayer.Cold, out var updatedPoint));
        Assert.Equal(postB, updatedPoint);
        Assert.True(StandHereTypes.IsNearPoint(testWorld.GetPosition(consciousPlayer.Id), postB));

        // Wake the incapacitated player without issuing a new command; Stand Here resumes and walks to postA.
        koPlayer.Cold.Medical!.WakeUp(null);
        DecisionSystem.Request(koPlayer.Cold);
        testWorld.RunTicks(60);

        Assert.True(StandHereTypes.IsNearPoint(testWorld.GetPosition(koPlayer.Id), postA));
    }

    [Fact]
    public void CharacterWalksToStandPointAndHoldsIdleWithoutRepathSpam()
    {
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var system);
        var guard = testWorld.SpawnPlayer("Guard", new Vector3(10f, 0f, 10f));
        var standPoint = new Vector3(50f, 0f, 10f);

        testWorld.World.Commands.Enqueue(new StandHereCommand([guard.Id], standPoint, Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(60);

        Assert.True(StandHereTypes.IsNearPoint(testWorld.GetPosition(guard.Id), standPoint));
        Assert.Equal(CharacterTask.Idle, testWorld.GetTask(guard.Id));

        var jobsSource = new StandHereJobs(system);
        for (int tick = 0; tick < 20; tick++)
        {
            testWorld.RunTicks(1);
            var check = jobsSource.CanRun(testWorld.World, guard.Slot, 0);
            Assert.True(check.Can);
            Assert.True(check.Running);
            Assert.Equal(CharacterTask.Idle, testWorld.GetTask(guard.Id));
        }
    }

    [Fact]
    public void OutOfRangeSkipsWithoutDisablingJobsLetsLowerJobRunAndResumesWhenRangeIncreasesOrZeroUnlimited()
    {
        var lowerStub = new StubLowerJobSystem();
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out var settings, out var bridge, out var system, lowerStub);
        var worker = testWorld.SpawnPlayer("Sentinel", new Vector3(10f, 0f, 10f));
        var standPoint = new Vector3(10f, 0f, 10f);

        testWorld.World.Commands.Enqueue(new StandHereCommand([worker.Id], standPoint, Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(1);
        worker.Cold.Jobs.Add(new PermaJob(LowerStubJobType, null, Hand.Null));
        testWorld.RunTicks(15);

        Assert.Equal(0, lowerStub.RunCount);

        // Teleport to 700m (7000 units >= 6520 units): Stand Here yields to StubLowerJob without disabling Jobs or dropping _points.
        testWorld.Teleport(worker.Id, new Vector3(7010f, 0f, 10f));
        testWorld.RunTicks(15);

        Assert.True(lowerStub.RunCount > 0);
        Assert.NotEqual(CharacterOrders.None, worker.Cold.Orders & CharacterOrders.Jobs);
        Assert.Equal(0, PermaJobs.IndexOf(worker.Cold, StandHereTypes.JobType));
        Assert.True(system.TryGetStandPoint(worker.Cold, out _));
        Assert.Equal("out of range", new StandHereJobs(system).CanRun(testWorld.World, worker.Slot, 0).Why);

        // Raise slider via Mods UI bridge to 1000m and pause lower job so Stand Here starts walking back immediately.
        lowerStub.ShouldRun = false;
        settings.MaxReturnDistanceMeters = 1000;
        bridge.MarkDirty();
        testWorld.RunTicks(5);

        Assert.Equal(CharacterTask.GoTo, testWorld.GetTask(worker.Id));

        // Lowering slider mid-walk below current distance halts the active Stand Here GoTo walk cleanly via Stop.
        settings.MaxReturnDistanceMeters = 10;
        bridge.MarkDirty();
        testWorld.RunTicks(5);

        Assert.Equal(CharacterTask.Idle, testWorld.GetTask(worker.Id));

        // Setting 0 (unlimited) allows returning even from 3000m (30000 units) away.
        testWorld.Teleport(worker.Id, new Vector3(30010f, 0f, 10f));
        settings.MaxReturnDistanceMeters = 0;
        bridge.MarkDirty();
        testWorld.RunTicks(5);

        Assert.Equal(CharacterTask.GoTo, testWorld.GetTask(worker.Id));
    }

    [Fact]
    public void RemovingStandHereJobPrunesPointAndCancelsLingeringWalk()
    {
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var system);
        var walker = testWorld.SpawnPlayer("Walker", new Vector3(10f, 0f, 10f));
        var farPoint = new Vector3(500f, 0f, 10f);

        testWorld.World.Commands.Enqueue(new StandHereCommand([walker.Id], farPoint, Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(5);

        Assert.Equal(CharacterTask.GoTo, testWorld.GetTask(walker.Id));
        Assert.True(system.TryGetStandPoint(walker.Cold, out _));

        walker.Cold.Jobs.RemoveAt(0);
        testWorld.RunTicks(2);

        Assert.False(system.TryGetStandPoint(walker.Cold, out _));
        Assert.Null(walker.Cold.Running);
        Assert.Equal(CharacterTask.Idle, testWorld.GetTask(walker.Id));
    }

    [Fact]
    public void JobSourceGuardsInvalidSlotAndJobIndexWithoutThrowing()
    {
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var system);
        var worker = testWorld.SpawnPlayer("Worker", new Vector3(10f, 0f, 10f));
        var jobSource = new StandHereJobs(system);

        Assert.Equal("missing character", jobSource.CanRun(testWorld.World, -1, 0).Why);
        Assert.Equal("missing character", jobSource.CanRun(testWorld.World, 9999, 0).Why);
        Assert.Equal("not Stand Here", jobSource.CanRun(testWorld.World, worker.Slot, -1).Why);
        Assert.Equal("not Stand Here", jobSource.CanRun(testWorld.World, worker.Slot, 99).Why);

        jobSource.Start(testWorld.World, -1, 0);
        jobSource.Start(testWorld.World, worker.Slot, 99);
        jobSource.Stop(testWorld.World, -1, 0);
        jobSource.Stop(testWorld.World, worker.Slot, 99);
    }

    static MeitouTestWorld CreateWorld(
        int maxReturnDistanceMeters,
        out StandHereSettings settings,
        out SettingsBridge bridge,
        out StandHereSystem system,
        StubLowerJobSystem? lowerStub = null)
    {
        settings = new StandHereSettings { MaxReturnDistanceMeters = maxReturnDistanceMeters };
        bridge = new SettingsBridge(settings);
        system = new StandHereSystem(bridge, NoOpLog);

        var systems = new List<ITickSystem> { system };
        if (lowerStub is not null)
            systems.Add(lowerStub);

        return new MeitouTestWorld(
            extensions =>
            {
                extensions.JobTypes[StandHereTypes.JobType] = StandHereTypes.JobName;
                if (lowerStub is not null)
                    extensions.JobTypes[LowerStubJobType] = "Lower Stub";
            },
            systems);
    }
}
