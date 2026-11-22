using Meitou.Data.Gameplay.Ai;
using Meitou.Simulation;

namespace StandingOrderJob;

static class StandHereTypes
{
    /// <summary>
    /// Claimed with <c>AddJobType</c>; Meitou mod codes start at <see cref="SimulationExtensions.FirstModCode"/>.
    /// </summary>
    public const TaskType Job = (TaskType)SimulationExtensions.FirstModCode;

    public const string JobName = "Stand Here";

    /// <summary>
    /// Horizontal metres: treat the character as holding the stand point.
    /// </summary>
    public const float NearRadius = 1.5f;

    public static bool NearPoint(System.Numerics.Vector3 position, System.Numerics.Vector3 point)
    {
        float dx = position.X - point.X;
        float dz = position.Z - point.Z;
        return dx * dx + dz * dz <= NearRadius * NearRadius;
    }
}
