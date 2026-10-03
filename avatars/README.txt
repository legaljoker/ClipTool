SPEAKER PICTURES (AVATARS)
==========================

Make one folder per person, named after them (or after the speaker id ClipTool
gives them, e.g. SPEAKER_1). Put one picture per expression inside:

  avatars/
    Dave/
      neutral.png      <- the only one you really need
      happy.png
      excited.png
      sad.png
      mad.png          <- annoyed / irritated
      angry.png        <- really angry / shouting
      surprised.png
      scared.png
      happy_talk.png   <- OPTIONAL "mouth open" version of any expression;
                          ClipTool flips between happy.png and happy_talk.png
                          while Dave is talking so he looks like he's speaking
      idle.png         <- OPTIONAL picture for when Dave is quiet ('all' layout)

Missing expressions fall back sensibly (angry -> mad -> neutral, excited ->
happy -> neutral, ...). PNGs with a transparent background look best.
Square images work best; others are fitted into a square.

Tell ClipTool who is who in config.yaml:

  speakers:
    names:
      SPEAKER_1: Dave
      SPEAKER_2: Sarah

Run "Speakers & expressions" once without pictures to see which speaker is
SPEAKER_1, SPEAKER_2... in REPORT.txt. If a line is given to the wrong person
or the wrong expression, fix it in the timeline .json file in the job folder
and run again with that timeline file.
