using System.Numerics;
using Meitou.Data.Gameplay.Ai;
using Meitou.Data.Gameplay.Bodies;
using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Bodies;
using Meitou.Simulation.Combat;
using Meitou.Simulation.Factions;
using Meitou.Simulation.Movement;
using Meitou.Simulation.Player;

namespace StandingOrderJob.Tests.Harness;

/// <summary>Minimal walkable flat-plane stub returning straight-line paths so movement and job tests run without Kenshi assets.</summary>
sealed class FlatWalkability : IWalkability
{
    /// <summary>Implements <see cref="IWalkability.GroundHeight(float, float)"/>.</summary>
    public float GroundHeight(float x, float z) => 0f;

    /// <summary>Implements <see cref="IWalkability.IsWalkable(float, float)"/>.</summary>
    public bool IsWalkable(float x, float z) => true;

    /// <summary>Implements <see cref="IWalkability.FindPath"/> with a direct two-point path.</summary>
    public PathResult FindPath(Vector3 from, Vector3 to) => new([from, to]);
}

/// <summary>Identifies a synthetic character spawned inside <see cref="MeitouTestWorld"/>.</summary>
public sealed record SpawnedCharacter(CharacterId Id, int Slot, CharacterCold Cold);

/// <summary>Data-free headless <see cref="Meitou.Simulation.World"/> fixture for integration and behavioral mod tests.</summary>
public sealed class MeitouTestWorld : IDisposable
{
    readonly PathService _paths;
    readonly MovementSystem _movement;

    /// <summary>Creates a deterministic single-thread test world with flat movement and any registered mod systems.</summary>
    public MeitouTestWorld(
        Action<SimulationExtensions>? configureExtensions = null,
        IEnumerable<ITickSystem>? modSystems = null,
        int capacity = 32,
        ulong seed = 1)
    {
        var walkability = new FlatWalkability();
        _paths = new PathService(walkability, synchronous: true);
        _movement = new MovementSystem(_paths);

        var extensions = new SimulationExtensions();
        configureExtensions?.Invoke(extensions);
        var player = new PlayerSystem(null) { Extensions = extensions };

        var systems = new List<ITickSystem> { _movement, player };
        if (modSystems is not null)
            systems.AddRange(modSystems);

        var settings = new WorldSettings
        {
            Seed = seed,
            Threads = 1,
            PublishSnapshots = false,
        };
        World = new World(settings, walkability, systems, new CharacterTable(capacity));
        World.Squads.Add(new Squad { Id = 1, TemplateId = "test-player-squad", Faction = 0, Name = "Player Squad" });
        World.Squads.Add(new Squad { Id = 2, TemplateId = "test-npc-squad", Faction = 1, Name = "NPC Squad" });
        World.Player.Faction = 0;
    }

    /// <summary>Underlying headless Meitou simulation world.</summary>
    public World World { get; }

    /// <summary>Spawns a player-faction character with an active table slot and optional health/animal flags.</summary>
    public SpawnedCharacter SpawnPlayer(
        string name,
        Vector3 position,
        bool isIncapacitated = false,
        bool isDead = false,
        bool isAnimal = false,
        CharacterOrders orders = Orders.PlayerDefaults) =>
        Spawn(name, position, isPlayer: true, faction: 0, squadId: 1, isIncapacitated, isDead, isAnimal, orders);

    /// <summary>Spawns a non-player character in faction 1.</summary>
    public SpawnedCharacter SpawnNpc(
        string name,
        Vector3 position,
        bool isIncapacitated = false,
        bool isDead = false) =>
        Spawn(name, position, isPlayer: false, faction: 1, squadId: 2, isIncapacitated, isDead, isAnimal: false, CharacterOrders.None);

    /// <summary>Halts any active walk, moves an existing character to <paramref name="position"/>, and requests AI re-evaluation.</summary>
    public void Teleport(CharacterId characterId, Vector3 position)
    {
        if (!World.Characters.TryResolveNext(characterId, out int slot))
            throw new InvalidOperationException($"Character {characterId} is not active.");
        if (World.Characters.Cold(slot) is not { } cold)
            throw new InvalidOperationException($"Character {characterId} is missing cold state.");

        TaskStart.Stand(World.Characters, slot);
        World.Characters.Place(slot, position, 0f, CharacterTask.Idle);
        cold.Running = null;
        DecisionSystem.Request(cold);
    }

    /// <summary>Advances the simulation by <paramref name="ticks"/> fixed ticks.</summary>
    public void RunTicks(int ticks) => World.RunTicks(ticks);

    /// <summary>Returns the current world position for <paramref name="characterId"/>.</summary>
    public Vector3 GetPosition(CharacterId characterId)
    {
        if (!World.Characters.TryResolveNext(characterId, out int slot))
            throw new InvalidOperationException($"Character {characterId} is not active.");

        return World.Characters.Next[slot].Position;
    }

    /// <summary>Returns the current hot <see cref="CharacterTask"/> for <paramref name="characterId"/>.</summary>
    public CharacterTask GetTask(CharacterId characterId)
    {
        if (!World.Characters.TryResolveNext(characterId, out int slot))
            throw new InvalidOperationException($"Character {characterId} is not active.");

        return (CharacterTask)World.Characters.Next[slot].Task;
    }

    /// <summary>Implements <see cref="IDisposable.Dispose"/>. Releases the world, movement system, and synchronous path service.</summary>
    public void Dispose()
    {
        World.Dispose();
        _movement.Dispose();
        _paths.Dispose();
    }

    /// <summary>Allocates a character table entry with minimal chest <see cref="HealthPart"/> state.</summary>
    SpawnedCharacter Spawn(
        string name,
        Vector3 position,
        bool isPlayer,
        int faction,
        int squadId,
        bool isIncapacitated,
        bool isDead,
        bool isAnimal,
        CharacterOrders orders)
    {
        var chest = new HealthPart(new BodyPartTemplate { StringId = "chest", Name = "Chest" }, 100, 100f);
        var medical = new MedicalState([chest]);
        if (isDead)
            medical.Kill(null);
        else if (isIncapacitated)
            medical.KnockOut(60f, null);

        var cold = new CharacterCold
        {
            Name = name,
            IsPlayer = isPlayer,
            Faction = faction,
            SquadId = squadId,
            Medical = medical,
            Orders = orders,
            RunSpeed = SpeedMode.Free,
            Fighter = isAnimal ? new Fighter(null) { IsAnimal = true } : null,
        };
        var hot = new CharacterHot
        {
            Alive = true,
            Position = position,
            MaxSpeed = 50f,
            WalkSpeed = 20f,
        };

        var characterId = World.Characters.Spawn(hot, cold, World.Tick);
        if (!World.Characters.TryResolveNext(characterId, out int slot))
            throw new InvalidOperationException($"Spawned character '{name}' could not be resolved.");

        return new SpawnedCharacter(characterId, slot, cold);
    }
}
