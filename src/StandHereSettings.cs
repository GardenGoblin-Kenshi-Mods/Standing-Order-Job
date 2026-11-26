using Meitou.Modding;
using Meitou.Simulation.Production;

namespace StandingOrderJob;

/// <summary>Mods-page options for Standing Order Job; simulation reads changes via <see cref="SettingsBridge"/>.</summary>
public sealed class StandHereSettings
{
    public const int MinDistanceMeters = 0;

    /// <summary>Default return range in metres matching Meitou's vanilla job range (<see cref="ProductionSystem.JobRangeSq"/>, ~651.7 m).</summary>
    public const int DefaultDistanceMeters = 652;

    public const int MaxDistanceMeters = 2000;

    /// <summary>Maximum 3D distance in metres a character walks back to their stand point (<c>0</c> disables the cap).</summary>
    [Setting("Max return distance (m)",
        Hint = "Maximum distance (in metres) a character will walk back to their Stand Here point. Beyond this range, Stand Here is skipped (leaving Jobs on) until the character returns within range. 0 = unlimited; 652 matches Meitou's vanilla job range.",
        Min = MinDistanceMeters, Max = MaxDistanceMeters, Step = 1,
        SubGroup = "Return distance")]
    public int MaxReturnDistanceMeters { get; set; } = DefaultDistanceMeters;
}
