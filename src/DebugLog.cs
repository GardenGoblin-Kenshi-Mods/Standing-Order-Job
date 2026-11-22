using Meitou.Modding;

namespace StandingOrderJob;

static class DebugLog
{
    public static Action<string> Of(ModContext context) =>
        Environment.GetEnvironmentVariable("MEITOU_STANDING_ORDER_JOB_DEBUG") == "1"
            ? context.Log
            : static _ => { };
}
