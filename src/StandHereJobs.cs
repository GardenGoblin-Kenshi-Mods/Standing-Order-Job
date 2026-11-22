using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Factions;
using Meitou.Simulation.Movement;

namespace StandingOrderJob;

/// <summary>
/// Walks to the stored stand point, then holds there while the job remains listed.
/// </summary>
sealed class StandHereJobs(StandHereSystem owner) : IJobSource
{
    public DecisionCheck CanRun(World world, int slot, int k)
    {
        var table = world.Characters;
        var cold = table.Cold(slot)!;
        if ((uint)k >= (uint)cold.Jobs.Count || cold.Jobs[k].Type != StandHereTypes.Job)
            return DecisionCheck.No("not Stand Here");

        if (SquadRegistry.IsAnimal(cold) || cold is not { IsPlayer: true, Medical: { Dead: false, Incapacitated: false } })
            return DecisionCheck.No("ineligible");

        if (!owner.TryPoint(cold, out var point))
            return DecisionCheck.No("no stand point");

        var task = (CharacterTask)table.Next[slot].Task;
        var pos = table.Next[slot].Position;

        if (cold.Running is { Source: TaskSource.Job, Row: StandHereTypes.Job } run && run.Job == k)
        {
            if (StandHereTypes.NearPoint(pos, point))
                return DecisionCheck.Runs;
            if (task == CharacterTask.GoTo && run.Runs(task))
                return DecisionCheck.Runs;
        }

        return DecisionCheck.Run;
    }

    public void Start(World world, int slot, int k)
    {
        var table = world.Characters;
        var cold = table.Cold(slot)!;
        if ((uint)k >= (uint)cold.Jobs.Count || cold.Jobs[k].Type != StandHereTypes.Job)
            return;
        if (!owner.TryPoint(cold, out var point))
            return;

        var pos = table.Next[slot].Position;
        if (StandHereTypes.NearPoint(pos, point))
        {
            owner.Log($"job hold: slot {slot} job {k} at point");
            TaskStart.Start(world, slot, new TaskRequest(CharacterTask.Idle, StandHereTypes.Job, TaskSource.Job)
            {
                Job = k,
            });
            return;
        }

        owner.Log($"job walk: slot {slot} job {k} to ({point.X:0.##},{point.Y:0.##},{point.Z:0.##})");
        if (DestinationAccess.WalkRefused(world, slot, StandHereTypes.Job, point))
        {
            owner.Log($"job walk refused: slot {slot}");
            return;
        }

        TaskStart.Start(world, slot, new TaskRequest(CharacterTask.GoTo, StandHereTypes.Job, TaskSource.Job)
        {
            Job = k,
            Point = point,
            Mode = cold.RunSpeed,
        });
    }

    public void Stop(World world, int slot, int k)
    {
        var table = world.Characters;
        var cold = table.Cold(slot)!;
        if (cold.Running is not { Source: TaskSource.Job, Row: StandHereTypes.Job } run || run.Job != k)
            return;

        owner.Log($"job stop: slot {slot} job {k}");
        if (table.Next[slot].Task == (byte)CharacterTask.GoTo)
            TaskStart.Stand(table, slot);
    }
}
