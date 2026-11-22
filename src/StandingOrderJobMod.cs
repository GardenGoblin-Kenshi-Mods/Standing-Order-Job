using Meitou.Modding;
using Meitou.Simulation;

namespace StandingOrderJob;

public sealed class StandingOrderJobMod : IMod
{
    public void Initialize(ModContext context)
    {
        var log = DebugLog.Of(context);
        context.AddJobType(StandHereTypes.Job, StandHereTypes.JobName);
        context.AddSystem("stand-here", _ => new StandHereSystem(log), SystemAnchor.AfterPart(StandardParts.Player));
        context.AddUi(new StandHereUi(log));
        log("initialized Stand Here job type 1000");
    }
}
