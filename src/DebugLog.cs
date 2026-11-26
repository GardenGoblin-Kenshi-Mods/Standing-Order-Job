using Meitou.Modding;

namespace StandingOrderJob;

/// <summary>Provides opt-in debug logging gated by <c>MEITOU_STANDING_ORDER_JOB_DEBUG=1</c>.</summary>
static class DebugLog
{
    /// <summary>Returns Meitou's mod logger when <c>MEITOU_STANDING_ORDER_JOB_DEBUG=1</c>, or a no-op delegate otherwise.</summary>
    public static Action<string> Of(ModContext context) =>
        Environment.GetEnvironmentVariable("MEITOU_STANDING_ORDER_JOB_DEBUG") == "1"
            ? context.Log
            : static _ => { };
}
