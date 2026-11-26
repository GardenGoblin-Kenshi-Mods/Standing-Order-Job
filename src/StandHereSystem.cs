using System.Numerics;
using System.Text;
using Meitou.Data.Save;
using Meitou.Modding;
using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Factions;
using Meitou.Simulation.Movement;
using Meitou.Simulation.Player;
using Meitou.Simulation.Production;

namespace StandingOrderJob;

/// <summary>Owns persistent Stand Here assignments, serial command handling, save serialization, and deterministic state hashing.</summary>
public sealed class StandHereSystem : ITickSystem, IChooserOwner, IModSaveData, IStateHashed
{
    const int SaveFormat = 1;

    readonly Dictionary<LastingId, Vector3> _points = [];
    readonly StandHereJobs _jobs;
    readonly SettingsBridge _bridge;
    readonly Action<string> _log;
    int _maxReturnDistanceMeters;

    /// <summary>Initializes the system with the settings bridge and the mod's opt-in debug logger.</summary>
    public StandHereSystem(SettingsBridge bridge, Action<string> log)
    {
        _bridge = bridge;
        _log = log;
        _maxReturnDistanceMeters = bridge.CurrentMaxReturnDistanceMeters;
        _jobs = new StandHereJobs(this);
    }

    internal Action<string> Log => _log;

    /// <summary>Implements <see cref="IChooserOwner.Register"/>. Binds <see cref="StandHereJobs"/> to <see cref="StandHereTypes.JobType"/>.</summary>
    public void Register(ChooserRegistry chooser) => chooser.Jobs(StandHereTypes.JobType, _jobs);

    /// <summary>Implements <see cref="ITickSystem.Inputs"/>. Applies pending settings, queued Stand Here commands in serial order, and prunes removed jobs.</summary>
    public void Inputs(World world, IReadOnlyList<SimCommand> commands)
    {
        if (_bridge.TryTake(out int maxReturnDistanceMeters))
        {
            bool hasChanged = maxReturnDistanceMeters != _maxReturnDistanceMeters;
            _maxReturnDistanceMeters = maxReturnDistanceMeters;
            _log(FormattableString.Invariant($"settings: MaxReturnDistanceMeters={_maxReturnDistanceMeters}"));
            if (hasChanged)
                RequestReplanForAssigned(world);
        }

        foreach (var command in commands.OfType<StandHereCommand>())
            Apply(world, command);

        PruneAndCancel(world);
    }

    /// <summary>Filters eligible living non-animal player recipients, then defers a one-shot move or assigns permanent Stand Here jobs.</summary>
    void Apply(World world, StandHereCommand command)
    {
        if (!StandHereTypes.IsFinitePoint(command.Point))
        {
            _log($"command refused: non-finite point at tick {command.Tick}");
            return;
        }

        var characters = world.Characters;
        var recipients = new List<CharacterId>();
        foreach (var characterId in command.Recipients)
        {
            bool hasSlot = characters.TryResolveNext(characterId, out int slot);
            CharacterCold? cold = hasSlot ? characters.Cold(slot) : null;
            if (!hasSlot || cold is null)
                continue;

            bool isLivingPlayer = cold is { IsPlayer: true, Medical: { Dead: false } };
            bool isAnimal = SquadRegistry.IsAnimal(cold);
            if (!isLivingPlayer || isAnimal)
                continue;

            recipients.Add(characterId);
        }

        if (recipients.Count == 0)
        {
            _log($"command refused: no eligible recipients at tick {command.Tick}");
            return;
        }

        _log($"command: permanent={command.Permanent} recipients={recipients.Count} "
            + $"point={StandHereTypes.FormatPoint(command.Point)} tick={command.Tick}");

        if (!command.Permanent)
        {
            world.Defer(new MoveOrder(recipients, command.Point) { Tick = world.Tick + 1 });
            _log($"deferred MoveOrder for {recipients.Count} at tick {world.Tick + 1}");
            return;
        }

        foreach (var characterId in recipients)
            AssignJob(world, characterId, command.Point);
    }

    /// <summary>Stores the character's stand point under its <see cref="LastingId"/>, adds or updates the Stand Here job in place, and requests AI re-evaluation.</summary>
    void AssignJob(World world, CharacterId characterId, Vector3 standPoint)
    {
        var characters = world.Characters;
        bool hasSlot = characters.TryResolveNext(characterId, out int slot);
        CharacterCold? cold = hasSlot ? characters.Cold(slot) : null;
        if (!hasSlot || cold is null)
            return;

        var lastingId = world.LastingIds.GetOrGive(characterId);
        if (lastingId.IsNone)
        {
            _log($"assign skipped: no lasting id for {characterId}");
            return;
        }

        _points[lastingId] = standPoint;

        int jobIndex = PermaJobs.IndexOf(cold, StandHereTypes.JobType);
        var job = new PermaJob(StandHereTypes.JobType, null, Hand.Null);
        if (jobIndex >= 0)
            cold.Jobs[jobIndex] = job;
        else
            cold.Jobs.Add(job);

        cold.Orders |= CharacterOrders.Jobs;
        DecisionSystem.Request(cold);
        _log($"assigned Stand Here lasting={lastingId.Value} slot={slot} point={StandHereTypes.FormatPoint(standPoint)}");
    }

    /// <summary>Drops stored stand points for loaded characters whose Stand Here job was removed (preserving unloaded characters) and cancels lingering job walks.</summary>
    void PruneAndCancel(World world)
    {
        if (_points.Count == 0)
            return;

        var characters = world.Characters;
        var toRemove = new List<LastingId>();
        foreach (var (lastingId, _) in _points)
        {
            var characterId = world.LastingIds.Find(lastingId);
            if (characterId.IsNone)
                continue;

            bool hasSlot = characters.TryResolveNext(characterId, out int slot);
            CharacterCold? cold = hasSlot ? characters.Cold(slot) : null;
            if (!hasSlot || cold is null)
            {
                toRemove.Add(lastingId);
                continue;
            }

            bool hasStandHereJob = PermaJobs.IndexOf(cold, StandHereTypes.JobType) >= 0;
            if (!hasStandHereJob)
            {
                toRemove.Add(lastingId);
                CancelGhostWalk(characters, slot, cold);
            }
        }

        foreach (var lastingId in toRemove)
        {
            _points.Remove(lastingId);
            _log($"pruned stand point lasting={lastingId.Value}");
        }
    }

    /// <summary>Stops an active Stand Here GoTo walk and clears <c>cold.Running</c> after the job row is removed.</summary>
    static void CancelGhostWalk(CharacterTable characters, int slot, CharacterCold cold)
    {
        bool isValidSlot = slot >= 0 && slot < characters.Next.Length;
        if (!isValidSlot)
            return;
        if (cold.Running is not { Source: TaskSource.Job, Row: StandHereTypes.JobType })
            return;

        ref readonly var hot = ref characters.Next[slot];
        if ((CharacterTask)hot.Task == CharacterTask.GoTo)
            TaskStart.Stand(characters, slot);
        cold.Running = null;
    }

    /// <summary>Looks up the assigned stand point for <paramref name="cold"/> via its persistent <see cref="LastingId"/>.</summary>
    internal bool TryGetStandPoint(CharacterCold cold, out Vector3 standPoint)
    {
        if (!cold.LastingId.IsNone && _points.TryGetValue(cold.LastingId, out standPoint))
            return true;
        standPoint = default;
        return false;
    }

    /// <summary>Returns whether <paramref name="currentPosition"/> is within the active return distance cap of <paramref name="standPoint"/>.</summary>
    internal bool IsWithinReturnRange(Vector3 currentPosition, Vector3 standPoint) =>
        StandHereTypes.IsWithinReturnRange(currentPosition, standPoint, _maxReturnDistanceMeters);

    /// <summary>Requests AI re-evaluation in deterministic <see cref="LastingId"/> order for loaded characters holding a Stand Here point.</summary>
    void RequestReplanForAssigned(World world)
    {
        if (_points.Count == 0)
            return;

        var characters = world.Characters;
        foreach (var lastingId in _points.Keys.OrderBy(id => id.Value))
        {
            var characterId = world.LastingIds.Find(lastingId);
            bool hasCharacter = !characterId.IsNone;
            int slot = -1;
            bool hasSlot = hasCharacter && characters.TryResolveNext(characterId, out slot);
            CharacterCold? cold = hasSlot ? characters.Cold(slot) : null;
            if (!hasSlot || cold is null)
                continue;

            DecisionSystem.Request(cold);
        }
    }

    /// <summary>Implements <see cref="IStateHashed.Hash"/>. Deterministically hashes settings and stored stand points sorted by <see cref="LastingId.Value"/>.</summary>
    public void Hash(ref StateHasher hasher)
    {
        hasher.Add(_maxReturnDistanceMeters);
        hasher.Add(_points.Count);
        foreach (var (lastingId, standPoint) in _points.OrderBy(entry => entry.Key.Value))
        {
            hasher.Add(lastingId.Value);
            hasher.Add(standPoint);
        }
    }

    /// <summary>Implements <see cref="IModSaveData.Write"/>. Serializes versioned (<c>SaveFormat = 1</c>) stand points sorted by <see cref="LastingId.Value"/>.</summary>
    public void Write(Stream stream)
    {
        using var writer = new BinaryWriter(stream, Encoding.UTF8, leaveOpen: true);
        writer.Write(SaveFormat);
        writer.Write(_points.Count);
        foreach (var (lastingId, standPoint) in _points.OrderBy(entry => entry.Key.Value))
        {
            writer.Write(lastingId.Value);
            writer.Write(standPoint.X);
            writer.Write(standPoint.Y);
            writer.Write(standPoint.Z);
        }
    }

    /// <summary>Implements <see cref="IModSaveData.Read"/>. Restores versioned stand points from save data, skipping empty IDs or non-finite coordinates.</summary>
    public void Read(Stream stream)
    {
        _points.Clear();
        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);
        int format = reader.ReadInt32();
        if (format != SaveFormat)
            return;

        int count = reader.ReadInt32();
        if (count < 0)
            return;
        for (int i = 0; i < count; i++)
        {
            var lastingId = new LastingId(reader.ReadUInt64());
            var standPoint = new Vector3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle());
            bool hasLastingId = !lastingId.IsNone;
            bool isFinitePoint = StandHereTypes.IsFinitePoint(standPoint);
            if (hasLastingId && isFinitePoint)
                _points[lastingId] = standPoint;
        }

        _log($"save restored {_points.Count} stand point(s)");
    }
}
