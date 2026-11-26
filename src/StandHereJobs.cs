using System.Diagnostics.CodeAnalysis;
using Meitou.Simulation;
using Meitou.Simulation.Ai;
using Meitou.Simulation.Factions;
using Meitou.Simulation.Movement;

namespace StandingOrderJob;

/// <summary>Walks to the stored stand point when within return range, then holds there while the job remains listed.</summary>
sealed class StandHereJobs(StandHereSystem system) : IJobSource
{
    /// <summary>Implements <see cref="IJobSource.CanRun"/>. Validates character/job eligibility and return distance, reporting whether Stand Here should start, keep holding/walking, or yield.</summary>
    public DecisionCheck CanRun(World world, int slot, int jobIndex)
    {
        var characters = world.Characters;
        if (!TryGetCharacterCold(characters, slot, out var cold))
            return DecisionCheck.No("missing character");

        if (!IsStandHereJob(cold, jobIndex))
            return DecisionCheck.No("not Stand Here");

        bool isConsciousPlayer = cold is { IsPlayer: true, Medical: { Dead: false, Incapacitated: false } };
        bool isAnimal = SquadRegistry.IsAnimal(cold);
        if (!isConsciousPlayer || isAnimal)
            return DecisionCheck.No("ineligible");

        if (!system.TryGetStandPoint(cold, out var standPoint))
            return DecisionCheck.No("no stand point");

        ref readonly var hot = ref characters.Next[slot];
        var currentTask = (CharacterTask)hot.Task;
        var currentPosition = hot.Position;
        if (!system.IsWithinReturnRange(currentPosition, standPoint))
            return DecisionCheck.No("out of range");

        if (MatchesRunningJob(cold, jobIndex))
        {
            // On arrival, Meitou switches the hot task to Idle while cold.Running can still record GoTo.
            // Check IsNearPoint alongside GoTo so DecisionSystem holds instead of restarting GoTo.
            bool isAtStandPoint = StandHereTypes.IsNearPoint(currentPosition, standPoint);
            bool isWalkingToStandPoint = currentTask == CharacterTask.GoTo
                && MatchesRunningJob(cold, jobIndex, currentTask);
            if (isAtStandPoint || isWalkingToStandPoint)
                return DecisionCheck.Runs;
        }

        return DecisionCheck.Run;
    }

    /// <summary>Implements <see cref="IJobSource.Start"/>. Starts an Idle hold if already at the stand point, or a GoTo walk when reachable.</summary>
    public void Start(World world, int slot, int jobIndex)
    {
        var characters = world.Characters;
        if (!TryGetCharacterCold(characters, slot, out var cold))
            return;
        if (!IsStandHereJob(cold, jobIndex))
            return;

        if (!system.TryGetStandPoint(cold, out var standPoint))
            return;

        ref readonly var hot = ref characters.Next[slot];
        var currentPosition = hot.Position;
        if (!system.IsWithinReturnRange(currentPosition, standPoint))
            return;

        if (StandHereTypes.IsNearPoint(currentPosition, standPoint))
        {
            system.Log($"job hold: slot {slot} job {jobIndex} at point");
            TaskStart.Start(world, slot, new TaskRequest(CharacterTask.Idle, StandHereTypes.JobType, TaskSource.Job)
            {
                Job = jobIndex,
            });
            return;
        }

        system.Log($"job walk: slot {slot} job {jobIndex} to {StandHereTypes.FormatPoint(standPoint)}");
        if (DestinationAccess.WalkRefused(world, slot, StandHereTypes.JobType, standPoint))
        {
            system.Log($"job walk refused: slot {slot}");
            return;
        }

        TaskStart.Start(world, slot, new TaskRequest(CharacterTask.GoTo, StandHereTypes.JobType, TaskSource.Job)
        {
            Job = jobIndex,
            Point = standPoint,
            Mode = cold.RunSpeed,
        });
    }

    /// <summary>Implements <see cref="IJobSource.Stop"/>. Halts an active Stand Here walk when the job is preempted or stopped.</summary>
    public void Stop(World world, int slot, int jobIndex)
    {
        var characters = world.Characters;
        if (!TryGetCharacterCold(characters, slot, out var cold))
            return;
        if (!MatchesRunningJob(cold, jobIndex))
            return;

        system.Log($"job stop: slot {slot} job {jobIndex}");
        ref readonly var hot = ref characters.Next[slot];
        if ((CharacterTask)hot.Task == CharacterTask.GoTo)
            TaskStart.Stand(characters, slot);
    }

    /// <summary>Validates <paramref name="slot"/> bounds against <see cref="CharacterTable.Next"/> and returns the active cold character record.</summary>
    static bool TryGetCharacterCold(CharacterTable characters, int slot, [NotNullWhen(true)] out CharacterCold? cold)
    {
        bool isValidSlot = slot >= 0 && slot < characters.Next.Length;
        if (!isValidSlot)
        {
            cold = null;
            return false;
        }

        cold = characters.Cold(slot);
        return cold is not null;
    }

    /// <summary>Returns whether <paramref name="jobIndex"/> points to a Stand Here entry in the character's permanent job list.</summary>
    static bool IsStandHereJob(CharacterCold cold, int jobIndex)
    {
        bool isValidIndex = jobIndex >= 0 && jobIndex < cold.Jobs.Count;
        if (!isValidIndex)
            return false;

        return cold.Jobs[jobIndex].Type == StandHereTypes.JobType;
    }

    /// <summary>Returns whether the character is currently running this Stand Here job entry, optionally requiring a specific active task.</summary>
    static bool MatchesRunningJob(CharacterCold cold, int jobIndex, CharacterTask? requiredTask = null)
    {
        if (cold.Running is not { Source: TaskSource.Job, Row: StandHereTypes.JobType } runningTask)
            return false;

        bool isMatchingJobIndex = runningTask.Job == jobIndex;
        bool matchesRequiredTask = requiredTask is not { } task || runningTask.Runs(task);
        return isMatchingJobIndex && matchesRequiredTask;
    }
}
