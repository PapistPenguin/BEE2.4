from datetime import datetime
from zipfile import ZipFile
from io import BytesIO

import os
import os.path
import stat
import shutil
import sys
import subprocess

from property_parser import Property
from BSP import BSP, BSP_LUMPS
import utils

LOGGER = utils.init_logging('bee2/VRAD.log')

CONF = Property('Config', [])
SCREENSHOT_DIR = os.path.join(
    '..',
    'portal2',  # This is hardcoded into P2, it won't change for mods.
    'puzzles',
    # Then the <random numbers> folder
)
# Locations of resources we need to pack
RES_ROOT = [
    os.path.join('..', loc)
    for loc in
    ('bee2', 'bee2_dev', 'portal2_dlc2')
]

GAME_FOLDER = {
    # The game's root folder, where screenshots are saved
    utils.STEAM_IDS['PORTAL2']: 'portal2',
    utils.STEAM_IDS['TWTM']: 'twtm',
    utils.STEAM_IDS['APTAG']: 'aperturetag',
}

SOUND_MAN_FOLDER = {
    # The folder where game_sounds_manifest is found
    utils.STEAM_IDS['PORTAL2']: 'portal2_dlc2',
    utils.STEAM_IDS['TWTM']: 'twtm',
    utils.STEAM_IDS['APTAG']: 'aperturetag',
}

# Files that VBSP may generate, that we want to insert into the packfile.
# They are all found in bee2/inject/.
INJECT_FILES = {
    # Defines choreo lines used on coop death, taunts, etc.
    'response_data.nut': 'scripts/vscripts/BEE2/coop_response_data.nut',

    # The list of soundscripts that the game loads.
    'soundscript_manifest.txt': 'scripts/game_sounds_manifest.txt',

    # A generated soundscript for the current music.
    'music_script.txt': 'scripts/BEE2_generated_music.txt'
}


# Various parts of the soundscript generated for BG music.

# The starting section defining the name and volume.
# SNDLVL_NONE means it's infinite range.
MUSIC_START = """\
"music.BEE2{name}"
 {
 "channel" "CHAN_STATIC"
 "soundlevel" "SNDLVL_NONE"
 "volume" "{vol}"
"""

# The basic operator stack for music without any additional tracks.
MUSIC_BASE = """\
 "soundentry_version" "2"
 "operator_stacks"
  {
  "update_stack"
   {
   "import_stack" "update_music_stereo"
"""

# Operator stacks which enable the given gel types.
MUSIC_GEL_BOUNCE_MAIN = """\

  "import_stack" "p2_update_music_play_gel"
  "gel_play_entry"
   {
   "entry_name" "music.BEE2_gel_bounce"
   }
  "gel_stop_entry"
   {
   "match_entry" "music.BEE2_gel_bounce"
   }
"""

MUSIC_GEL_SPEED_MAIN = """\

  "import_stack" "p2_update_music_play_speed_gel"
  "speed_velocity_trigger"
   {
   "input2" "250"
   }
   "speed_play_entry"
    {
    "entry_name" "music.BEE2_gel_speed"
    }
   "speed_stop_entry"
    {
    "match_entry" "music.BEE2_gel_speed"
    }
"""

MUSIC_FUNNEL_MAIN = """\

  "import_stack" "p2_update_music_play_tbeam"
  "play_entry"
   {
   "entry_name" "music.BEE2_funnel"
   }
  "stop_entry"
   {
   "match_entry" "music.BEE2_funnel"
   }
"""

# The gel operator stack syncronises the music with the base track.
MUSIC_GEL_STACK = """\

 "soundentry_version" "2"
 "operator_stacks"
  {
  "start_stack"
   {
   "import_stack" "start_sync_to_entry"
   "elapsed_time"
    {
    "entry" "music.BEE2"
    }
   "duration_div"
    {
    "input2" "1"
    }
   "div_mult"
    {
    "input1" "1.0"
    }
   }
  "update_stack"
   {
   "import_stack" "update_music_stereo"
   "volume_fade_in"
    {
     "input_max" "0.25"
    }
   "volume_fade_out"
    {
    "input_max" "1.0"
    }
   }
  }
 }
"""

# The funnel operator statck makes it start randomly offset into the music..
MUSIC_FUNNEL_STACK = """\

 "soundentry_version" "2"
 "operator_stacks"
  {
  "start_stack"
   {
   "random_offset"
    {
    "operator" "math_random"
    "input_min" "0.0"
    "input_max" "126"
    }
   "negative_delay"
    {
    "operator" "math_float"
    "apply" "mult"
    "input1" "@random_offset.output"
    "input2" "-1.0"
    }
   "delay_output"
    {
    "operator" "sys_output"
    "input_float" "@negative_delay.output"
    "output" "delay"
    }
   }
  "update_stack"
   {
   "import_stack" "update_music_stereo"
   "mixer"
    {
    "mixgroup" "unduckedMusic"
    }
   "volume_fade_in"
    {
    "input_max" "3.0"
    "input_map_min" "0.05"
    }
   "volume_fade_out"
    {
    "input_max" "0.75"
    "input_map_min" "0.05"
    }
   "volume_lfo_time_scale"
    {
    "input2" "0.3"
    }
   "volume_lfo_scale"
    {
    "input2" "0.4"
    }
   }
  }
 }
"""


def quote(txt):
    return '"' + txt + '"'


def set_readonly(file):
    """Make the given file read-only."""
    # Get the old flags
    flags = os.stat(file).st_mode
    # Make it read-only
    os.chmod(
        file,
        flags & ~
        stat.S_IWUSR & ~
        stat.S_IWGRP & ~
        stat.S_IWOTH
    )


def unset_readonly(file):
    """Set the writeable flag on a file."""
    # Get the old flags
    flags = os.stat(file).st_mode
    # Make it writeable
    os.chmod(
        file,
        flags |
        stat.S_IWUSR |
        stat.S_IWGRP |
        stat.S_IWOTH
    )


def load_config():
    global CONF
    LOGGER.info('Loading Settings...')
    try:
        with open("bee2/vrad_config.cfg") as config:
            CONF = Property.parse(config, 'bee2/vrad_config.cfg').find_key(
                'Config', []
            )
    except FileNotFoundError:
        pass
    LOGGER.info('Config Loaded!')


def pack_file(zipfile: ZipFile, filename):
    """Check multiple locations for a resource file.
    """
    if '\t' in filename:
        # We want to rename the file!
        filename, arcname = filename.split('\t')
    else:
        arcname = filename

    for poss_path in RES_ROOT:
        full_path = os.path.normpath(
            os.path.join(poss_path, filename)
        )
        if os.path.isfile(full_path):
            zipfile.write(
                filename=full_path,
                arcname=arcname,
            )
            break
    else:
        LOGGER.warning('"bee2/' + filename + '" not found!')


def gen_sound_manifest(additional, has_music=False):
    """Generate a new game_sounds_manifest.txt file.

    This includes all the current scripts defined, plus any custom ones.
    """
    orig_manifest = os.path.join(
        '..',
        SOUND_MAN_FOLDER.get(CONF['game_id', ''], 'portal2'),
        'scripts',
        'game_sounds_manifest.txt',
    )
    try:
        with open(orig_manifest) as f:
            props = Property.parse(f, orig_manifest).find_key(
                'game_sounds_manifest', [],
            )
    except FileNotFoundError:  # Assume no sounds
        props = Property('game_sounds_manifest', [])

    scripts = [prop.value for prop in props.find_all('precache_file')]

    for script in additional:
        scripts.append(script)


    # Build and unbuild it to strip other things out - Valve includes a bogus
    # 'new_sound_scripts_must_go_below_here' entry..
    new_props = Property('game_sounds_manifest', [
        Property('precache_file', file)
        for file in scripts
    ])

    inject_loc = os.path.join('bee2', 'inject', 'soundscript_manifest.txt')
    with open(inject_loc, 'w') as f:
        for line in new_props.export():
            f.write(line)
    LOGGER.info('Written new soundscripts_manifest..')


def generate_music_script(data: Property):
    """Generate a soundscript file for music."""
    funnel = data.find_key('tbeam', '')
    bounce = data.find_key('bouncegel', '')
    speed = data.find_key('speedgel', '')

    with open(os.path.join('bee2', 'inject', 'music_script.txt'), 'w') as file:
        # Write the base music track
        file.write(MUSIC_START.format(name='', vol='1'))
        write_sound(file, data.find_key('base'), snd_prefix='#*')
        file.write(MUSIC_BASE)
        # The 'soundoperators' section is still open now.

        # Add the operators to play the auxilluary sounds..
        if funnel.value:
            file.write(MUSIC_FUNNEL_MAIN)
        if bounce.value:
            file.write(MUSIC_GEL_BOUNCE_MAIN)
        if speed.value:
            file.write(MUSIC_GEL_SPEED_MAIN)

        # End the main sound block
        file.write("  }\n }\n}\n")

        if funnel.value:
            # Write the 'music.BEE2_funnel' sound entry
            file.write(MUSIC_START.format(name='_funnel', vol='1'))
            write_sound(file, funnel, snd_prefix='*')
            file.write(MUSIC_FUNNEL_STACK)

        if bounce.value:
            file.write(MUSIC_START.format(name='_gel_bounce', vol='0.5'))
            write_sound(file, bounce, snd_prefix='*')
            file.write(MUSIC_GEL_STACK)

        if speed.value:
            file.write(MUSIC_START.format(name='_gel_speed', vol='0.5'))
            write_sound(file, speed, snd_prefix='*')
            file.write(MUSIC_GEL_STACK)


def write_sound(file, snds: Property, snd_prefix='*'):
    """Write either a single sound, or multiple rndsound.

    snd_prefix is the prefix for each filename - *, #, @, etc.
    """
    if snds.has_children():
        file.write(' "rndwave"\n  {\n')
        for snd in snds:
            file.write('  "wave" "{sndchar}{file}"\n'.format(
                file=snd.value,
                sndchar=snd_prefix,
            )
        )
        file.write('  }\n')
    else:
        file.write(
            ' "wave" "{sndchar}{file}"\n'.format(
                file=snds.value,
                sndchar=snd_prefix,
            )
        )


def inject_files(zipfile: ZipFile):
    """Inject certain files into the packlist,  if they exist."""
    for filename, arcname in INJECT_FILES.items():
        filename = os.path.join('bee2', 'inject', filename)
        if os.path.exists(filename):
            LOGGER.info('Injecting "{}" into packfile.', arcname)
            zipfile.write(filename, arcname)


def pack_content(path, is_peti):
    """Pack any custom content into the map.

    Filelist format: "[control char]filename[\t packname]"
    Filename is the name of the actual file. If given packname is the
    name to save it into the packfile as. If the first character of the
    filename is '#', the file will be added to the soundscript manifest too.
    """
    files = set()  # Files to pack.
    soundscripts = set()  # Soundscripts need to be added to the manifest too..
    try:
        pack_list = open(path[:-4] + '.filelist.txt')
    except (IOError, FileNotFoundError):
        pass
    else:
        with pack_list:
            for line in pack_list:
                line = line.strip().lower()
                if not line or line.startswith('//'):
                    continue  # Skip blanks or comments
                if line[:1] == '#':
                    line = line[1:]
                    soundscripts.add(line)

                files.add(line)

    if not files:
        LOGGER.info('No files to pack!')
        return

    LOGGER.info('Files to pack:')
    for file in sorted(files):
        LOGGER.info(' # "' + file + '"')

    LOGGER.info("Packing Files!")
    bsp_file = BSP(path)
    LOGGER.debug(' - Header read')
    bsp_file.read_header()

    # Manipulate the zip entirely in memory
    zip_data = BytesIO()
    zip_data.write(bsp_file.get_lump(BSP_LUMPS.PAKFILE))
    zipfile = ZipFile(zip_data, mode='a')
    LOGGER.debug(' - Existing zip read')

    for file in files:
        pack_file(zipfile, file)

    # Only generate a soundscript for PeTI maps..
    has_music = False
    if is_peti:
        music_data = CONF.find_key('MusicScript', [])
        if music_data.value:
            generate_music_script(music_data)
            # Add the new script to the manifest file..
            soundscripts.add('scripts/BEE2_generated_music.txt')

    gen_sound_manifest(soundscripts)

    inject_files(zipfile)

    LOGGER.debug(' - Added files')

    zipfile.close()  # Finalise the zip modification

    # Copy the zipfile into the BSP file, and adjust the headers
    bsp_file.replace_lump(
        path,
        BSP_LUMPS.PAKFILE,
        zip_data.getvalue(),  # Get the binary data we need
    )
    LOGGER.debug(' - BSP written!')

    LOGGER.info("Packing complete!")


def find_screenshots():
    """Find candidate screenshots to overwrite."""
    # Inside SCREENSHOT_DIR, there should be 1 folder with a
    # random name which contains the user's puzzles. Just
    # attempt to modify a screenshot in each of the directories
    # in the folder.
    for folder in os.listdir(SCREENSHOT_DIR):
        full_path = os.path.join(SCREENSHOT_DIR, folder)
        if os.path.isdir(full_path):
            # The screenshot to modify is untitled.jpg
            screenshot = os.path.join(full_path, 'untitled.jpg')
            if os.path.isfile(screenshot):
                yield screenshot


def mod_screenshots():
    """Modify the map's screenshot."""
    mod_type = CONF['screenshot_type', 'PETI'].lower()

    if mod_type == 'cust':
        LOGGER.info('Using custom screenshot!')
        scr_loc = CONF['screenshot', '']
    elif mod_type == 'auto':
        LOGGER.info('Using automatic screenshot!')
        scr_loc = None
        # The automatic screenshots are found at this location:
        auto_path = os.path.join(
            '..',
            GAME_FOLDER.get(CONF['game_id', ''], 'portal2'),
            'screenshots'
        )
        # We need to find the most recent one. If it's named
        # "previewcomplete", we want to ignore it - it's a flag
        # to indicate the map was playtested correctly.
        try:
            screens = [
                os.path.join(auto_path, path)
                for path in
                os.listdir(auto_path)
            ]
        except FileNotFoundError:
            # The screenshot folder doesn't exist!
            screens = []
        screens.sort(
            key=os.path.getmtime,
            reverse=True,
            # Go from most recent to least
        )
        playtested = False
        for scr_shot in screens:
            filename = os.path.basename(scr_shot)
            if filename.startswith('bee2_playtest_flag'):
                # Previewcomplete is a flag to indicate the map's
                # been playtested. It must be newer than the screenshot
                playtested = True
                continue
            elif filename.startswith('bee2_screenshot'):
                continue # Ignore other screenshots

            # We have a screenshot. Check to see if it's
            # not too old. (Old is > 2 hours)
            date = datetime.fromtimestamp(
                os.path.getmtime(scr_shot)
            )
            diff = datetime.now() - date
            if diff.total_seconds() > 2 * 3600:
                LOGGER.info(
                    'Screenshot "{scr}" too old ({diff!s})',
                    scr=scr_shot,
                    diff=diff,
                )
                continue

            # If we got here, it's a good screenshot!
            LOGGER.info('Chosen "{}"', scr_shot)
            LOGGER.info('Map Playtested: {}', playtested)
            scr_loc = scr_shot
            break
        else:
            # If we get to the end, we failed to find an automatic
            # screenshot!
            LOGGER.info('No Auto Screenshot found!')
            mod_type = 'peti'  # Suppress the "None not found" error

        if utils.conv_bool(CONF['clean_screenshots', '0']):
            LOGGER.info('Cleaning up screenshots...')
            # Clean up this folder - otherwise users will get thousands of
            # pics in there!
            for screen in screens:
                if screen != scr_loc:
                    os.remove(screen)
            LOGGER.info('Done!')
    else:
        # PeTI type, or something else
        scr_loc = None

    if scr_loc is not None and os.path.isfile(scr_loc):
        # We should use a screenshot!
        for screen in find_screenshots():
            LOGGER.info('Replacing "{}"...', screen)
            # Allow us to edit the file...
            unset_readonly(screen)
            shutil.copy(scr_loc, screen)
            # Make the screenshot readonly, so P2 can't replace it.
            # Then it'll use our own
            set_readonly(screen)

    else:
        if mod_type != 'peti':
            # Error if we were looking for a screenshot
            LOGGER.warning('"{}" not found!', scr_loc)
        LOGGER.info('Using PeTI screenshot!')
        for screen in find_screenshots():
            # Make the screenshot writeable, so P2 will replace it
            LOGGER.info('Making "{}" replaceable...', screen)
            unset_readonly(screen)


def run_vrad(args):
    "Execute the original VRAD."

    if utils.MAC:
        os_suff = '_osx'
    elif utils.LINUX:
        os_suff = '_linux'
    else:
        os_suff = ''

    joined_args = (
        '"' + os.path.normpath(
            os.path.join(os.getcwd(), "vrad" + os_suff + "_original")
            ) +
        '" ' +
        " ".join(
            # put quotes around args which contain spaces
            (quote(x) if " " in x else x)
            for x in args
            )
        )
    LOGGER.info("Calling original VRAD...")
    LOGGER.info(joined_args)
    code = subprocess.call(
        joined_args,
        stdout=None,
        stderr=subprocess.PIPE,
        shell=True,
    )
    if code == 0:
        LOGGER.info("Done!")
    else:
        LOGGER.warning("VRAD failed! (" + str(code) + ")")
        sys.exit(code)


def main(argv):
    LOGGER.info('BEE2 VRAD hook started!')
    args = " ".join(argv)
    fast_args = argv[1:]
    full_args = argv[1:]

    # The path is the last argument to vrad
    # P2 adds wrong slashes sometimes, so fix that.
    fast_args[-1] = path = os.path.normpath(argv[-1])

    LOGGER.info("Map path is " + path)
    if path == "":
        raise Exception("No map passed!")

    load_config()

    for a in fast_args[:]:
        if a.casefold() in (
                "-both",
                "-final",
                "-staticproplighting",
                "-staticproppolys",
                "-textureshadows",
                ):
            # remove final parameters from the modified arguments
            fast_args.remove(a)
        elif a in ('-force_peti', '-force_hammer', '-no_pack'):
            # we need to strip these out, otherwise VBSP will get confused
            fast_args.remove(a)
            full_args.remove(a)

    fast_args = ['-bounce', '2', '-noextra'] + fast_args

    # Fast args: -bounce 2 -noextra -game $gamedir $path\$file
    # Final args: -both -final -staticproplighting -StaticPropPolys
    # -textureshadows  -game $gamedir $path\$file

    if not path.endswith(".bsp"):
        path += ".bsp"

    if '-force_peti' in args or '-force_hammer' in args:
        # we have override command!
        if '-force_peti' in args:
            LOGGER.warning('OVERRIDE: Applying cheap lighting!')
            is_peti = True
        else:
            LOGGER.warning('OVERRIDE: Preserving args!')
            is_peti = False
    else:
        # If we don't get the special -force args, check for the name
        # equalling preview to determine if we should convert
        # If that is false, check the config file to see what was
        # specified there.
        is_peti = (
            os.path.basename(path) == "preview.bsp" or
            utils.conv_bool(CONF['force_full'], False)
        )

    mod_screenshots()

    if is_peti:
        LOGGER.info("Forcing Cheap Lighting!")
        run_vrad(fast_args)
    else:
        LOGGER.info("Hammer map detected! Not forcing cheap lighting..")
        run_vrad(full_args)

    if '-no_pack' not in args:
        pack_content(path, is_peti)
    else:
        LOGGER.warning("No items to pack!")
    LOGGER.info("BEE2 VRAD hook finished!")

if __name__ == '__main__':
    main(sys.argv)
