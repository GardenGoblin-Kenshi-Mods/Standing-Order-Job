using System.Numerics;
using Meitou.Simulation;

namespace StandingOrderJob;

/// <summary>Orders selected player characters to <see cref="Point"/> once, or assigns a permanent Stand Here job when <see cref="Permanent"/>.</summary>
public sealed record StandHereCommand(
    IReadOnlyList<CharacterId> Recipients,
    Vector3 Point,
    bool Permanent) : SimCommand;
