using Meitou.Data.Fcs;

if (args.Length != 1)
{
    Console.Error.WriteLine("Usage: StandingOrderJobPackage <output .mod path>");
    return 2;
}

var mod = new FcsFile
{
    Author = "Garden Goblin",
    Description = "Per-character Stand Here permanent job via hold-right-click context menu.",
    NextId = 1,
};
FcsWriter.WriteFile(mod, args[0]);
return 0;
