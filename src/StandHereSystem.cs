using System.Numerics;
using Meitou.Data.Save;
using Meitou.Modding;
using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Factions;
using Meitou.Simulation.Movement;
using Meitou.Simulation.Player;
using Meitou.Simulation.Production;

namespace StandingOrderJob;

public sealed class StandHereSystem : ITickSystem, IDecisionOwner, IModSaveData, IStateHashed
{
    const int SaveFormat = 1;

    readonly Dictionary<LastingId, Vector3> _points = [];
    readonly StandHereJobs _jobs;
    readonly Action<string> _log;

    public StandHereSystem(Action<string> log)
    {
        _log = log;
        _jobs = new StandHereJobs(this);
    }

    internal Action<string> Log => _log;

    public void RegisterSources(DecisionSystem decisions) => decisions.RegisterJob(StandHereTypes.Job, _jobs);

    public void Inputs(World world, IReadOnlyList<SimCommand> commands)
    {
        foreach (var command in commands.OfType<StandHereCommand>())
            Apply(world, command);

        PruneAndCancel(world);
    }

    void Apply(World world, StandHereCommand command)
    {
        if (!Finite(command.Point))
        {
            _log($"command refused: non-finite point at tick {command.Tick}");
            return;
        }

        var recipients = new List<CharacterId>();
        foreach (var id in command.Recipients)
        {
            if (!world.Characters.TryResolveNext(id, out int slot)
                || world.Characters.Cold(slot) is not { IsPlayer: true, Medical: { Dead: false } } cold
                || SquadRegistry.IsAnimal(cold))
                continue;
            recipients.Add(id);
        }

        if (recipients.Count == 0)
        {
            _log($"command refused: no eligible recipients at tick {command.Tick}");
            return;
        }

        _log($"command: permanent={command.Permanent} recipients={recipients.Count} "
            + $"point=({command.Point.X:0.##},{command.Point.Y:0.##},{command.Point.Z:0.##}) tick={command.Tick}");

        if (!command.Permanent)
        {
            world.Defer(new MoveOrder(recipients, command.Point) { Tick = world.Tick + 1 });
            _log($"deferred MoveOrder for {recipients.Count} at tick {world.Tick + 1}");
            return;
        }

        foreach (var id in recipients)
            AssignJob(world, id, command.Point);
    }

    void AssignJob(World world, CharacterId id, Vector3 point)
    {
        if (!world.Characters.TryResolveNext(id, out int slot) || world.Characters.Cold(slot) is not { } cold)
            return;

        var lasting = world.LastingIds.GetOrGive(id);
        if (lasting.IsNone)
        {
            _log($"assign skipped: no lasting id for {id}");
            return;
        }

        _points[lasting] = point;

        int index = PermaJobs.IndexOf(cold, StandHereTypes.Job);
        var job = new PermaJob(StandHereTypes.Job, null, Hand.Null);
        if (index >= 0)
            cold.Jobs[index] = job;
        else
            cold.Jobs.Add(job);

        cold.Orders |= CharacterOrders.Jobs;
        DecisionSystem.Request(cold);
        _log($"assigned Stand Here lasting={lasting.Value} slot={slot} point=({point.X:0.##},{point.Y:0.##},{point.Z:0.##})");
    }

    void PruneAndCancel(World world)
    {
        if (_points.Count == 0) return;

        var drop = new List<LastingId>();
        foreach (var (lasting, _) in _points)
        {
            var character = world.LastingIds.Find(lasting);
            if (character.IsNone) continue;

            if (!world.Characters.TryResolveNext(character, out int slot) || world.Characters.Cold(slot) is not { } cold)
            {
                drop.Add(lasting);
                continue;
            }

            if (PermaJobs.IndexOf(cold, StandHereTypes.Job) < 0)
            {
                drop.Add(lasting);
                CancelGhostWalk(world, slot, cold);
            }
        }

        foreach (var id in drop)
        {
            _points.Remove(id);
            _log($"pruned stand point lasting={id.Value}");
        }
    }

    static void CancelGhostWalk(World world, int slot, CharacterCold cold)
    {
        if (cold.Running is not { Source: TaskSource.Job, Row: StandHereTypes.Job })
            return;
        if (world.Characters.Next[slot].Task == (byte)CharacterTask.GoTo)
            TaskStart.Stand(world.Characters, slot);
        cold.Running = null;
    }

    internal bool TryPoint(CharacterCold cold, out Vector3 point)
    {
        if (!cold.LastingId.IsNone && _points.TryGetValue(cold.LastingId, out point))
            return true;
        point = default;
        return false;
    }

    public void Hash(ref StateHasher hasher)
    {
        hasher.Add(_points.Count);
        foreach (var (id, point) in _points.OrderBy(e => e.Key.Value))
        {
            hasher.Add(id.Value);
            hasher.Add(point);
        }
    }

    public void Write(Stream stream)
    {
        using var writer = new BinaryWriter(stream, System.Text.Encoding.UTF8, leaveOpen: true);
        writer.Write(SaveFormat);
        writer.Write(_points.Count);
        foreach (var (id, point) in _points.OrderBy(e => e.Key.Value))
        {
            writer.Write(id.Value);
            writer.Write(point.X);
            writer.Write(point.Y);
            writer.Write(point.Z);
        }
    }

    public void Read(Stream stream)
    {
        _points.Clear();
        using var reader = new BinaryReader(stream, System.Text.Encoding.UTF8, leaveOpen: true);
        int format = reader.ReadInt32();
        if (format != SaveFormat) return;

        int count = reader.ReadInt32();
        for (int i = 0; i < count; i++)
        {
            var id = new LastingId(reader.ReadUInt64());
            var point = new Vector3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle());
            if (!id.IsNone && Finite(point))
                _points[id] = point;
        }

        _log($"save restored {_points.Count} stand point(s)");
    }

    static bool Finite(Vector3 point) =>
        float.IsFinite(point.X) && float.IsFinite(point.Y) && float.IsFinite(point.Z);
}
