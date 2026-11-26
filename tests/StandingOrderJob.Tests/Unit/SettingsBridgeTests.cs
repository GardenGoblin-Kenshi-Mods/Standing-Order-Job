using Xunit;

namespace StandingOrderJob.Tests.Unit;

/// <summary>Layer 1 unit tests for thread safety and clamping in <see cref="SettingsBridge"/>.</summary>
public sealed class SettingsBridgeTests
{
    [Theory]
    [InlineData(-50, StandHereSettings.MinDistanceMeters)]
    [InlineData(0, 0)]
    [InlineData(652, 652)]
    [InlineData(99999, StandHereSettings.MaxDistanceMeters)]
    public void InitialStateClampsConfiguredReturnDistance(int configuredMeters, int expectedMeters)
    {
        var settings = new StandHereSettings { MaxReturnDistanceMeters = configuredMeters };
        var bridge = new SettingsBridge(settings);

        Assert.Equal(expectedMeters, bridge.CurrentMaxReturnDistanceMeters);
        Assert.True(bridge.TryTake(out int drained));
        Assert.Equal(expectedMeters, drained);
        Assert.False(bridge.TryTake(out _));
    }

    [Fact]
    public void MarkDirtyUpdatesValueAndReArmsTryTake()
    {
        var settings = new StandHereSettings();
        var bridge = new SettingsBridge(settings);
        Assert.True(bridge.TryTake(out _));

        settings.MaxReturnDistanceMeters = 1250;
        bridge.MarkDirty();

        Assert.Equal(1250, bridge.CurrentMaxReturnDistanceMeters);
        Assert.True(bridge.TryTake(out int taken));
        Assert.Equal(1250, taken);
        Assert.False(bridge.TryTake(out _));
    }

    [Fact]
    public async Task ConcurrentMarkDirtyAndTryTakeRemainConsistentAndClamped()
    {
        var settings = new StandHereSettings();
        var bridge = new SettingsBridge(settings);

        var cancellationToken = TestContext.Current.CancellationToken;
        var writerTask = Task.Run(() =>
        {
            for (int iteration = -20; iteration <= 2500; iteration += 7)
            {
                settings.MaxReturnDistanceMeters = iteration;
                bridge.MarkDirty();
            }
        }, cancellationToken);

        var readerTask = Task.Run(() =>
        {
            for (int iteration = 0; iteration < 500; iteration++)
            {
                if (bridge.TryTake(out int distance))
                {
                    Assert.InRange(distance, StandHereSettings.MinDistanceMeters, StandHereSettings.MaxDistanceMeters);
                }

                Assert.InRange(
                    bridge.CurrentMaxReturnDistanceMeters,
                    StandHereSettings.MinDistanceMeters,
                    StandHereSettings.MaxDistanceMeters);
            }
        }, cancellationToken);

        await Task.WhenAll(writerTask, readerTask);

        int finalValue = bridge.CurrentMaxReturnDistanceMeters;
        Assert.InRange(finalValue, StandHereSettings.MinDistanceMeters, StandHereSettings.MaxDistanceMeters);
    }
}
