using System.Numerics;
using System.Text;
using Meitou.Data.Save;
using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Movement;
using Meitou.Simulation.Production;
using StandingOrderJob.Tests.Harness;
using Xunit;

namespace StandingOrderJob.Tests.Integration;

/// <summary>Layer 2 integration tests covering save/reload round-trips, unloaded-zone retention, deserialization guards, and deterministic state hashing.</summary>
public sealed class StandHerePersistenceTests
{
    static readonly Action<string> NoOpLog = static _ => { };

    [Fact]
    public void SaveReloadRoundTripRestoresStandPointAndResumesHolding()
    {
        var savedBytes = Array.Empty<byte>();
        var standPoint = new Vector3(60f, 0f, 10f);
        LastingId originalLastingId;

        using (var worldA = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var systemA))
        {
            var alpha = worldA.SpawnPlayer("Alpha", new Vector3(10f, 0f, 10f));
            worldA.World.Commands.Enqueue(new StandHereCommand([alpha.Id], standPoint, Permanent: true)
            {
                Tick = worldA.World.Tick,
            });
            worldA.RunTicks(5);

            originalLastingId = alpha.Cold.LastingId;
            Assert.False(originalLastingId.IsNone);

            using var stream = new MemoryStream();
            systemA.Write(stream);
            savedBytes = stream.ToArray();
        }

        using var worldB = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var systemB);
        var reloadedAlpha = worldB.SpawnPlayer("Alpha", new Vector3(10f, 0f, 10f));
        var restoredLastingId = worldB.World.LastingIds.GetOrGive(reloadedAlpha.Id);
        Assert.Equal(originalLastingId, restoredLastingId);

        reloadedAlpha.Cold.Jobs.Add(new PermaJob(StandHereTypes.JobType, null, Hand.Null));
        using (var readStream = new MemoryStream(savedBytes))
        {
            systemB.Read(readStream);
        }

        DecisionSystem.Request(reloadedAlpha.Cold);
        worldB.RunTicks(60);

        Assert.True(StandHereTypes.IsNearPoint(worldB.GetPosition(reloadedAlpha.Id), standPoint));
        Assert.Equal(CharacterTask.Idle, worldB.GetTask(reloadedAlpha.Id));
    }

    [Fact]
    public void UnloadedCharacterPointSurvivesIntermediateSaveAndResumesOnLaterReload()
    {
        var alphaPoint = new Vector3(50f, 0f, 10f);
        var bravoPoint = new Vector3(80f, 0f, 10f);
        byte[] saveAfterUnloadedTicks;
        LastingId alphaLastingId;
        LastingId bravoLastingId;

        byte[] initialSave;
        using (var worldA = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var systemA))
        {
            var alpha = worldA.SpawnPlayer("Alpha", new Vector3(10f, 0f, 10f));
            var bravo = worldA.SpawnPlayer("Bravo", new Vector3(10f, 0f, 20f));
            worldA.World.Commands.Enqueue(new StandHereCommand([alpha.Id], alphaPoint, Permanent: true)
            {
                Tick = worldA.World.Tick,
            });
            worldA.World.Commands.Enqueue(new StandHereCommand([bravo.Id], bravoPoint, Permanent: true)
            {
                Tick = worldA.World.Tick,
            });
            worldA.RunTicks(5);

            alphaLastingId = alpha.Cold.LastingId;
            bravoLastingId = bravo.Cold.LastingId;
            using var stream = new MemoryStream();
            systemA.Write(stream);
            initialSave = stream.ToArray();
        }

        // Reload only Alpha; Bravo remains unloaded (LastingIds.Find returns None) across multiple PruneAndCancel ticks.
        using (var worldB = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var systemB))
        {
            var alpha = worldB.SpawnPlayer("Alpha", new Vector3(10f, 0f, 10f));
            Assert.Equal(alphaLastingId, worldB.World.LastingIds.GetOrGive(alpha.Id));
            alpha.Cold.Jobs.Add(new PermaJob(StandHereTypes.JobType, null, Hand.Null));

            using (var stream = new MemoryStream(initialSave))
            {
                systemB.Read(stream);
            }

            Assert.True(worldB.World.LastingIds.Find(bravoLastingId).IsNone);
            DecisionSystem.Request(alpha.Cold);
            worldB.RunTicks(10);

            using var outStream = new MemoryStream();
            systemB.Write(outStream);
            saveAfterUnloadedTicks = outStream.ToArray();
        }

        // Reload both Alpha and Bravo together; Bravo's unloaded post is still preserved and resumes walking.
        using (var worldC = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out var systemC))
        {
            var alpha = worldC.SpawnPlayer("Alpha", alphaPoint);
            var bravo = worldC.SpawnPlayer("Bravo", new Vector3(10f, 0f, 20f));
            Assert.Equal(alphaLastingId, worldC.World.LastingIds.GetOrGive(alpha.Id));
            Assert.Equal(bravoLastingId, worldC.World.LastingIds.GetOrGive(bravo.Id));

            alpha.Cold.Jobs.Add(new PermaJob(StandHereTypes.JobType, null, Hand.Null));
            bravo.Cold.Jobs.Add(new PermaJob(StandHereTypes.JobType, null, Hand.Null));

            using (var stream = new MemoryStream(saveAfterUnloadedTicks))
            {
                systemC.Read(stream);
            }

            DecisionSystem.Request(alpha.Cold);
            DecisionSystem.Request(bravo.Cold);
            worldC.RunTicks(80);

            Assert.True(StandHereTypes.IsNearPoint(worldC.GetPosition(alpha.Id), alphaPoint));
            Assert.True(StandHereTypes.IsNearPoint(worldC.GetPosition(bravo.Id), bravoPoint));
        }
    }

    [Fact]
    public void ReadRejectsInvalidFormatsNegativeCountsEmptyIdsAndNonFiniteCoordinates()
    {
        var bridge = new SettingsBridge(new StandHereSettings());
        var system = new StandHereSystem(bridge, NoOpLog);
        ulong emptyHash = HashOf(new StandHereSystem(new SettingsBridge(new StandHereSettings()), NoOpLog));

        using (var wrongFormat = BuildPayload(format: 99, entries: [(1UL, new Vector3(1f, 2f, 3f))]))
        {
            system.Read(wrongFormat);
            Assert.Equal(emptyHash, HashOf(system));
        }

        using (var negativeCount = new MemoryStream())
        {
            using (var writer = new BinaryWriter(negativeCount, Encoding.UTF8, leaveOpen: true))
            {
                writer.Write(1);
                writer.Write(-5);
            }

            negativeCount.Position = 0;
            system.Read(negativeCount);
            Assert.Equal(emptyHash, HashOf(system));
        }

        using (var mixedPayload = BuildPayload(format: 1, entries:
        [
            (0UL, new Vector3(5f, 0f, 5f)),
            (10UL, new Vector3(float.NaN, 0f, 1f)),
            (11UL, new Vector3(0f, float.PositiveInfinity, 1f)),
            (12UL, new Vector3(20f, 0f, 30f)),
        ]))
        {
            system.Read(mixedPayload);
            var reference = new StandHereSystem(new SettingsBridge(new StandHereSettings()), NoOpLog);
            using var refStream = BuildPayload(format: 1, entries: [(12UL, new Vector3(20f, 0f, 30f))]);
            reference.Read(refStream);
            Assert.Equal(HashOf(reference), HashOf(system));
        }
    }

    [Fact]
    public void StateHashIsOrderIndependentAcrossLoadsAndReflectsMaxReturnDistanceChanges()
    {
        var systemOrderA = new StandHereSystem(new SettingsBridge(new StandHereSettings()), NoOpLog);
        var systemOrderB = new StandHereSystem(new SettingsBridge(new StandHereSettings()), NoOpLog);

        using var streamA = BuildPayload(1, [(1UL, new Vector3(10f, 0f, 10f)), (2UL, new Vector3(30f, 0f, 40f))]);
        using var streamB = BuildPayload(1, [(2UL, new Vector3(30f, 0f, 40f)), (1UL, new Vector3(10f, 0f, 10f))]);
        systemOrderA.Read(streamA);
        systemOrderB.Read(streamB);

        Assert.Equal(HashOf(systemOrderA), HashOf(systemOrderB));

        var customSettings = new StandHereSettings { MaxReturnDistanceMeters = 250 };
        var systemDifferentRange = new StandHereSystem(new SettingsBridge(customSettings), NoOpLog);
        using var streamC = BuildPayload(1, [(1UL, new Vector3(10f, 0f, 10f)), (2UL, new Vector3(30f, 0f, 40f))]);
        systemDifferentRange.Read(streamC);

        Assert.NotEqual(HashOf(systemOrderA), HashOf(systemDifferentRange));
    }

    [Fact]
    public void RepeatedHeadlessRunsProduceIdenticalWorldStateHashes()
    {
        ulong hashRunA = RunStandardSequenceHash();
        ulong hashRunB = RunStandardSequenceHash();

        Assert.Equal(hashRunA, hashRunB);
    }

    static ulong RunStandardSequenceHash()
    {
        using var testWorld = CreateWorld(StandHereSettings.DefaultDistanceMeters, out _, out _, out _);
        var alpha = testWorld.SpawnPlayer("Alpha", new Vector3(10f, 0f, 10f));
        var bravo = testWorld.SpawnPlayer("Bravo", new Vector3(20f, 0f, 10f));
        testWorld.World.Commands.Enqueue(new StandHereCommand([alpha.Id, bravo.Id], new Vector3(45f, 0f, 15f), Permanent: true)
        {
            Tick = testWorld.World.Tick,
        });
        testWorld.RunTicks(50);
        return testWorld.World.StateHash();
    }

    static MeitouTestWorld CreateWorld(
        int maxReturnDistanceMeters,
        out StandHereSettings settings,
        out SettingsBridge bridge,
        out StandHereSystem system)
    {
        settings = new StandHereSettings { MaxReturnDistanceMeters = maxReturnDistanceMeters };
        bridge = new SettingsBridge(settings);
        system = new StandHereSystem(bridge, NoOpLog);
        return new MeitouTestWorld(
            extensions => extensions.JobTypes[StandHereTypes.JobType] = StandHereTypes.JobName,
            [system]);
    }

    static MemoryStream BuildPayload(int format, IReadOnlyList<(ulong LastingId, Vector3 Point)> entries)
    {
        var stream = new MemoryStream();
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true))
        {
            writer.Write(format);
            writer.Write(entries.Count);
            foreach (var (lastingId, point) in entries)
            {
                writer.Write(lastingId);
                writer.Write(point.X);
                writer.Write(point.Y);
                writer.Write(point.Z);
            }
        }

        stream.Position = 0;
        return stream;
    }

    static ulong HashOf(StandHereSystem system)
    {
        var hasher = new StateHasher();
        system.Hash(ref hasher);
        return hasher.Value;
    }
}
