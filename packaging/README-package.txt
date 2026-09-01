Commander Quest Mod Tool
========================

Run CommanderQuestModTool to start. Nothing needs installing, and you do not
need Python or a compiler.


First launch
------------

The tool asks for two things.

1. Where Commander Quest is installed. It usually finds this on its own; if
   not, browse to the folder that contains Commander/Content/Paks.

2. The archive key for your version of the game. The game assembles this key
   while it runs rather than storing it, so it has to be read out of the
   running game once:

     - start Commander Quest and wait for the main menu
     - press "Recover key from the running game"

   The key is the same for everyone playing a given version, so this is a
   one-time step. You only need to do it again if a game patch changes it.

   The key is not shipped with these tools on purpose. It belongs to the game,
   so you take it from your own copy.

   Reading another program's memory is a privileged operation:

     Windows   run the tool as administrator for this step
     Linux     run this once:  sudo sysctl -w kernel.yama.ptrace_scope=0

Settings are saved to cqmod_config.local.json beside the program, so this only
happens once. You can change them later from the Setup button in the toolbar.


Making a mod
------------

Pick an asset, edit it on the tabs to the right, then press "Build & Install
Mod". Restart the game to see the change.

The Randomizer tab generates a whole randomized run from a checklist. The Mods
tab turns installed mods on and off without deleting them.

Mods are ordinary .pak files written to the game's Paks folder. Nothing else on
disk is modified, and deleting a mod pak fully undoes it.


Documentation and source
------------------------

https://github.com/ShrikeGames/commander_quest_modding_tools

Licensed GPL-3.0. Includes ooz (GPL-3.0) for Oodle Kraken decompression.
