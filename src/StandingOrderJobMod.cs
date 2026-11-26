using Meitou.Modding;
using Meitou.Simulation;

namespace StandingOrderJob;

/// <summary>Meitou mod entry point for Standing Order Job.</summary>
public sealed class StandingOrderJobMod : IMod
{
    /// <summary>Implements <see cref="IMod.Initialize"/>. Registers settings, claims the Stand Here job type, and registers the simulation system and UI extension.</summary>
    public void Initialize(ModContext context)
    {
        var log = DebugLog.Of(context);
        var settings = new StandHereSettings();
        var group = context.AddSettings(settings);
        var bridge = new SettingsBridge(settings);
        group.Changed += _ => bridge.MarkDirty();

        context.AddJobType(StandHereTypes.JobType, StandHereTypes.JobName);
        context.AddSystem("stand-here", _ => new StandHereSystem(bridge, log), SystemAnchor.AfterPart(StandardParts.Player));
        context.AddUi(new StandHereUi(log));
        log(FormattableString.Invariant(
            $"initialized {StandHereTypes.JobName}; job type {(int)StandHereTypes.JobType}; MaxReturnDistanceMeters={bridge.CurrentMaxReturnDistanceMeters}"));
    }
}
