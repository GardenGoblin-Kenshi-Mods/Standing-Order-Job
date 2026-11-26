using System.Numerics;
using Xunit;

namespace StandingOrderJob.Tests.Unit;

/// <summary>Layer 1 unit tests for pure geometry, range checks, and formatting helpers in <see cref="StandHereTypes"/>.</summary>
public sealed class StandHereTypesTests
{
    [Theory]
    [InlineData(0f, 0f, 0f, true)]
    [InlineData(1.5f, 250f, 0f, true)]
    [InlineData(1.1f, 0f, 1.0f, true)]
    [InlineData(1.6f, 0f, 0f, false)]
    public void IsNearPointUsesHorizontalRadiusAndIgnoresHeight(float x, float y, float z, bool expected)
    {
        var currentPosition = new Vector3(x, y, z);
        var standPoint = Vector3.Zero;

        Assert.Equal(expected, StandHereTypes.IsNearPoint(currentPosition, standPoint));
    }

    [Fact]
    public void IsWithinReturnRangeEnforcesStrictThreeDimensionalBoundaryInDecimetres()
    {
        var standPoint = new Vector3(10f, 0f, 10f);
        float boundaryUnits = StandHereSettings.DefaultDistanceMeters * StandHereTypes.WorldUnitsPerMeter;

        Assert.True(StandHereTypes.IsWithinReturnRange(
            standPoint + new Vector3(boundaryUnits - 1f, 0f, 0f),
            standPoint,
            StandHereSettings.DefaultDistanceMeters));
        Assert.False(StandHereTypes.IsWithinReturnRange(
            standPoint + new Vector3(boundaryUnits, 0f, 0f),
            standPoint,
            StandHereSettings.DefaultDistanceMeters));
        Assert.False(StandHereTypes.IsWithinReturnRange(
            standPoint + new Vector3(0f, boundaryUnits + 5f, 0f),
            standPoint,
            StandHereSettings.DefaultDistanceMeters));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void IsWithinReturnRangeAllowsAnyDistanceWhenCapDisabled(int disabledLimit)
    {
        var standPoint = Vector3.Zero;
        var distantPoint = new Vector3(1_000_000f, 5_000f, -1_000_000f);

        Assert.True(StandHereTypes.IsWithinReturnRange(distantPoint, standPoint, disabledLimit));
    }

    [Fact]
    public void IsWithinReturnRangeReturnsFalseForNonFiniteCoordinatesWhenCapped()
    {
        Assert.False(StandHereTypes.IsWithinReturnRange(
            new Vector3(float.NaN, 0f, 0f),
            Vector3.Zero,
            StandHereSettings.DefaultDistanceMeters));
    }

    [Fact]
    public void IsFinitePointRejectsNaNAndInfinities()
    {
        Assert.True(StandHereTypes.IsFinitePoint(new Vector3(-12.5f, 0f, 44.25f)));
        Assert.False(StandHereTypes.IsFinitePoint(new Vector3(float.NaN, 0f, 0f)));
        Assert.False(StandHereTypes.IsFinitePoint(new Vector3(0f, float.PositiveInfinity, 0f)));
        Assert.False(StandHereTypes.IsFinitePoint(new Vector3(0f, 0f, float.NegativeInfinity)));
    }

    [Fact]
    public void FormatPointUsesInvariantDecimalFormatting()
    {
        string formatted = StandHereTypes.FormatPoint(new Vector3(12.25f, -0.5f, 7f));
        Assert.Equal("(12.25,-0.5,7)", formatted);
    }
}
