using System.Numerics;
using Meitou.Data.Gameplay.Ai;
using Meitou.Simulation;

namespace StandingOrderJob;

/// <summary>Shared constants, geometry helpers, and debug formatting for Stand Here.</summary>
static class StandHereTypes
{
    /// <summary>Claimed with <c>AddJobType</c>; allocated in the Garden Goblin reserved block (<c>400_000+</c>), well above <see cref="SimulationExtensions.FirstModCode"/> (<c>1000</c>) to avoid collisions with other mods.</summary>
    public const TaskType JobType = (TaskType)400_000;

    public const string JobName = "Stand Here";

    /// <summary>Horizontal metres: treat the character as holding the stand point.</summary>
    public const float NearRadius = 1.5f;

    /// <summary>Meitou simulation coordinates use decimetres (<c>10</c> world units per metre).</summary>
    public const float WorldUnitsPerMeter = 10f;

    /// <summary>Returns whether <paramref name="currentPosition"/> is within <see cref="NearRadius"/> horizontal (XZ) metres of <paramref name="standPoint"/>.</summary>
    public static bool IsNearPoint(Vector3 currentPosition, Vector3 standPoint)
    {
        float dx = currentPosition.X - standPoint.X;
        float dz = currentPosition.Z - standPoint.Z;
        return dx * dx + dz * dz <= NearRadius * NearRadius;
    }

    /// <summary>Returns whether <paramref name="currentPosition"/> is within <paramref name="maxReturnDistanceMeters"/> 3D metres of <paramref name="standPoint"/> (<c>&lt;= 0</c> is unlimited).</summary>
    public static bool IsWithinReturnRange(Vector3 currentPosition, Vector3 standPoint, int maxReturnDistanceMeters)
    {
        if (maxReturnDistanceMeters <= 0)
            return true;

        float maxDistanceUnits = maxReturnDistanceMeters * WorldUnitsPerMeter;
        float maxDistanceSquared = maxDistanceUnits * maxDistanceUnits;
        return Vector3.DistanceSquared(currentPosition, standPoint) < maxDistanceSquared;
    }

    /// <summary>Returns whether all three components of <paramref name="point"/> are finite numbers.</summary>
    public static bool IsFinitePoint(Vector3 point) =>
        float.IsFinite(point.X) && float.IsFinite(point.Y) && float.IsFinite(point.Z);

    /// <summary>Formats a world coordinate compactly using invariant culture for opt-in debug logs.</summary>
    public static string FormatPoint(Vector3 point) =>
        FormattableString.Invariant($"({point.X:0.##},{point.Y:0.##},{point.Z:0.##})");
}
