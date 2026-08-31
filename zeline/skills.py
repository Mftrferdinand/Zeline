"""Skill system Zeline dengan scope public vs private.

Zeline dapat berjalan sebagai bot publik. Maka prosedur yang dibuat pemilik
(misalnya berisi path internal atau runbook privat) tidak boleh otomatis
tersedia untuk orang yang chat bot.

Layout data user:

    ~/.zeline/skills/
      public/     # skill bawaan aman yang dapat dibaca gateway `safe`
      private/    # skill pemilik; hanya profile `full` CLI owner

Install legacy memakai ``~/.zeline/skills/*.md``. Saat pertama
kali modul ini dipakai, skill legacy dipindahkan ke ``private/`` secara
konservatif agar tidak ada prosedur lama yang tidak sengaja terekspos.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from zeline import config

SKILLS_ROOT = config.DATA_DIR / "skills"
PUBLIC_SKILLS_DIR = SKILLS_ROOT / "public"
PRIVATE_SKILLS_DIR = SKILLS_ROOT / "private"
MIGRATION_MARKER = SKILLS_ROOT / ".scope-migrated-v1"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Skill berbentuk folder: satu entry point + subfolder pendukung yang boleh
# ditulis agent. Daftar ini juga membatasi ``manage_skill`` supaya tidak ada
# file liar di dalam direktori data.
SKILL_ENTRY = "SKILL.md"
SKILL_SUBDIRS = ("references", "templates", "scripts", "assets")
MAX_SKILL_CHARS = 100_000

# Named obsolete bundled skills. Delete only byte-identical seeded copies;
# user-customized files with these names survive.
LEGACY_BUNDLED_SKILL_DIGESTS: dict[str, tuple[str, ...]] = {
    'tmdb-media-web-maintenance.md': (
        '35f51a79be0c313bec2ec3f014200a00beeee7938173b5a20ccae0e5b62b8a4d',
    ),
}

# SHA-256 of each exact retired filename maps to every content digest that
# shipped under it. This preserves safe upgrades without retaining old product
# branding in source or deleting user-modified copies.
RETIRED_BUNDLED_SKILL_DIGESTS: dict[str, tuple[str, ...]] = {
    # Removed NBA betting workflow, LF and CRLF package revisions.
    '57d5f28520c4381518d7026e54afb9d453c4a262a1c6fa22f5653c663d33927c': (
        '56eef747ca4dd3335fe414b54a611beb107cf8ac607f320fd18377e0c93d1f40',
        '414d753de16b33eb73d938cbe6b7ecf36420eee635151e388c0dad6a73248bb4',
    ),
    '018864414fc7306f86d36e84700d19fc95cf9c177091ae6e453b759ff498e4b8': (
        'b1382e23fcf8c5242d673eecae8a590c4c3421f24051f4fc5f060575da9e926b',
    ),
    '08b84d2bbed4245d3a0ffc1e978d54b8c85e2f839a69c487db68cbb5a2804cbe': (
        '8603ee38f9ad689769e7705f8aab7470805d89ca07b984892bb0ab23aeee522f',
    ),
    '0ceedae8d949c4a93c25717773f6eff2b57471174a5898e90a3deb628b07202a': (
        'af81d46d749a027d5bf57be1ef1d3d9ed3767151e1ef7a98738fda0e15f128b5',
    ),
    '12901b81f7af682a703dc0463efb9224f99e5183ebc57821954b8e794676d592': (
        'af1725ca642794811e73d077d48759989b78721aa85ec3df18e9d0dd5b07de5e',
        'd8475fb9e46a11b90b4a87492e57e33d84d800806b66ccf06edd175e709a0f42',
    ),
    '13ff6e69e4c14c8a4a9b680741b3fe14a78c9c37e63429a2d88e117534398a03': (
        '47c049698a3e0cedd5be7ce16c562f6b1f239ba6eb4bbfb807393c3ad57a00b4',
    ),
    '141561a544b95cca70c2101b80b8775401c8ac47422a24fc4815387c0c061339': (
        '048f511e02bf3894ff9936d73b9836a7401c4e5acf33fd8290d1ec5844bdd606',
    ),
    '16c5c82e87da7fbf205c63d3d984bbf87200eb1f4cb8addc5df5a62edb1c4a32': (
        'a3b687b5f56c91f3529c05cfebc66116b3854a7203a9ed0ba2067903280ba2d8',
    ),
    '1783cad3994895700292bba72fed6a3913b8502ebf3e900f79b8b278d0c1bff7': (
        '9064595b2c3c7102ea9886d8df69244e2bca1cd252302f76b1399ca361c03a29',
        'ecd8927ec170d79a3716342a7aa4706214527ad65b25b58385c553c95626f0c5',
    ),
    '19b51357efafbbdf52e11a96d916e2f7457379d6b4d73bd60084d158b1d68b22': (
        '8741f448ed787ef0651e92e219d914dad2d048b0ec894179c8f3c4779362417e',
    ),
    '1dcd4725d08c8fbd1ff65ed344d4e86fabd093ff649969bc39de3eec5c12be0d': (
        '26a000f9324e69378c572ad24c05c674868c6f8c3bf420f73fe84d19498a738b',
    ),
    '22d3f903746ac7e032e732da4c4d77dbd65f7b6a48f8290cf4f82dca5676c207': (
        'a1be80bdf3c5f862bbe035579a638e384bec676ac453150d9d97409f816238c9',
    ),
    '2501bb032b0edf1c6901b1589b5be17709440ca566f5a53da48cde262e0f8a3c': (
        '4747b8dfcd365d40f91d92c525d6266d3dac300172f6a5312076e43a5f95481e',
    ),
    '2947c762170a1f509f16253c4a967c25a2f17117ae9a3c987d0ebd36118cec5a': (
        '8e8f7d0638e906edbd59f791b73b2b3db63396d8e35657452be170188986ef39',
    ),
    '2c8b7fe367a3f12ba500590cc696faf3f97a9fb491beaf5195c21590f97fe321': (
        'c340800fdb3008df0c3c8e42d9cbfc6be5a916eeb94dba25df4aee946e588517',
    ),
    '2dc3a788183a94fc789ee22dd89604a1cbc8fe6dfa09e493b7e3178dda5294aa': (
        '42f2ec8f191cc17ff83d462c87e3df756519c44f64c594cad6ac0c2737c0a219',
    ),
    '34ca6088104f5785209be556a18249e422d1b7381ba438b29416dd3f58c4a5f6': (
        'dbd55c2a39b5fe166b63a3ae22f80a2ac723193fb67f396dd51b39ada9e9d519',
    ),
    '3706384f4f3f848d3e1a5c5ef761edb888e4992dee8b161ff754d7290f9b8bc2': (
        '6b09c2b1107601ac20df4530b5ec7f07f859465d3e3d8b791a9db57772e69602',
    ),
    '3a59b55b7c79e0f1c33f27fb9bd4e5f769a404561a4b0ca4e679a55a4d67625a': (
        'a95d8a757585d05b5cd77dd9f1478a4049674547585679ef74aeeb734944bb0f',
    ),
    '3a74789ffac7c0e490ae15d54405e11fbb849348c6ce513b62425096c68dc78e': (
        '88d64749ff3487bb3bba4e2673a1656bf3519db96fc72cbe67f948a66b90b720',
    ),
    '3b1ed48a066f3b15746ab009de608fb77fd37618f0259919fbd8a7e308692c36': (
        'b52eac809bde2e4aa0798103e5d9dd5d2b67f1e517ac22d5498aabbe577ec1b2',
    ),
    '3dfa5c5706e3e5c6bd09ff7201d20d41132db42c40509fbe89f5b622d74cbfe7': (
        'e6b0ff2276e1222963ded51ec61a6e7cb832693671dbdb86712f57bb2d212ad5',
    ),
    '3f79305eb4696561802461807e7a3cd540249bfc2ef178033607232df3115ce9': (
        '6c9676d94a65578f834ab02f3329183026bc87e38eed2d3c654bec9286abfc3c',
    ),
    '424f5c10453c61800349e680613d06f1f2500cf646a3452a83084d9e8f9d3bb0': (
        '0569220a265b05b42bf80e64951e7697659455f7afb7205b98abc6d367047304',
    ),
    '4d182536a201c270174d4b3d361ddfaa267dbe73dd986e9334baca84b269c384': (
        '58a526f9209c24a5a6253b3a8ac757a47e9be159a7804338d0c9757a93f673fa',
    ),
    '56b8a8fad58859c43eb8ed279da1dbab2ae8708300d514587e34d7caa5c803b8': (
        '06fe8db77f98029c485042f4c4a575ac8a0b9f4ee7746528dc2d4594f24e694d',
    ),
    '5894256becee39a10e3184098df1adfb25a8ebbb5c23466b9e76db6452061936': (
        '74e2b688472b0f786eb7ca2682b9eee90b540ff8f65f5cd335a1f8f7f9766e86',
    ),
    '625211a490d9da9a3be21d5dc04a2d95fc996581d7969ac8177e178411d87cd7': (
        '9ec5afd003ac4cd8abb3120953f3c5e2086755036da7d735dd5d7fa5d18823c4',
    ),
    '6a6b33c5338bc04b7ce125806163754c91aa39dd0915a368e388dbe1103cf3ef': (
        'f97142650a19039417e5a05cffca1deb1d236efc7b5e51178086d67e87cc5097',
    ),
    '7136287ae85c5855033241363b1da9d155d49f8eca941e6a867544a005e07988': (
        '98d3df0c8dca86e7bbb4dccc963ec7d69485c40e6540e5cf2ea3f5311258b429',
    ),
    '77a68e902a2a9ba13d76dd4723e7890071427d7575909111de5b5667c0a3ffd3': (
        '0ec5c5e83dbeecb9d3356f1d6d4ef3950092158020fe6d699ee54b7fa3c8774d',
        '877f08cbb89ab01581bfc8d602e399a2bcebaa81325f77e6fad2ea0391b8faca',
    ),
    '7cd922075e418ec9301471d9be307506c3a50ca05a325e5a283b90e687c6becb': (
        'ede5343a294f5824919d7dde4a8cf3542cbc3594dc0cf7762ce654597abe8e9e',
    ),
    '7daac381ac7bf6387e1ec8c419973d6559276de6c954e9617aa66de73c710b2e': (
        '7e11f0da3fdf0dff2aa5b89adeaf4009c30b2d6ac5004dc0ac38d75069e697a6',
    ),
    '7f4372a52f6d2722f0cc2cbcc0e9969f6012e86755bb959d647eb9d072ced9d0': (
        'b7e4f423e315fb2df410600b6efdf7904e726cff0198904cc8ed687e13d3f69c',
    ),
    '84f91b72b73a8600fa37d03bebe88d4e5eada403eee6f181a61cf5eec7aa9d9b': (
        'f81c12870cc23e232446424b5f8d285c4732c52fe579f3100f3280fb2f628ddd',
    ),
    '8e1dc451abe0f72d624e923b8ffc5bad21ff0f4c019a2d6cd29a8305abac270f': (
        '28687ba4f334cf032a52da710b747069619a83d8865f88987c01b0942bae39df',
    ),
    '94ff285dfee62a77688cbe5f732632d30fc8023d7388c3b65bd55cf612a6319c': (
        'b3182f0c278b0ab9f6d76aa393b75a48d352fda6794414c936ec6a764e894c9c',
    ),
    '963022ad997acc627c8629bd8c8e8e0ffb70788fd00add8464649ba5e6edd28a': (
        '9adaacf0a2f81454608e34f68d9a94a35fd2440658015243808587ac1f97fd6c',
    ),
    '9d3ef551a62c924055c1734b4ad33d0ba686af295ba01da4d6f4590f30cfe1b1': (
        '45cfb2516cdf759ff4ff83ebbfa8308adbfda71291158bbf9e4daa8520939597',
    ),
    'b132ce52e623a1c01464edc4f7c1f5638fa803af436f72b2158c56377d36a030': (
        '20c292e4bf199407c060892ee6fa6cd92495a9b37f674659d3c91d5648b54951',
    ),
    'b33be176fea8fca5267195a8594c88172f3371354287b3a6ee875a53b923fdca': (
        'f748aa8393b393bae32fd5eed43d3b092d775727fda040b6fa2fe63f53c058a0',
    ),
    'bc330f976e67980dddc52ad77ab58a017355a0d8d45efd7418410d3ba1139d83': (
        '7b0f46ff112c194839debbe419757c747ed60213b40d1a1e00d2eef58ee77a80',
    ),
    'bdb17aa69ce6089c2bdc14dba6c147a919ed07464d4b0ee53b126f52e2ae346d': (
        '1a52d122f88d48a28d7246a98141629784f8bb38d17c71b5cc4981092b13c6d4',
    ),
    'bfde70e32fe563cdf60a4612335e2e594d63e53b06c6600eade6d740a49cd4d2': (
        '59e0f5fbbb0687f3df91e5da2142d3bc72017da159b58534430385c6807978ee',
    ),
    'c2090122eae03a735b711118d417053a72dd802beb04d235f84814a8904fd79e': (
        '7e8e6dd5150a3f5274616fc53dee492ecdd778e3cbcb213ad87d7f0227013979',
    ),
    'c79cbdd38ae5ad1632d0a6b1c94a7ce6147d252f426cade8bbe54bfd352bed3c': (
        'e5359af0e7881abd4a42f5f9cb612ff7a8bc459bf56291ab08a41e4dc8e9dfd0',
    ),
    'c9e5a0ec7b47a3b62ebd99140844ef0f89940fcfe04df61a52e5d31f8bb41c83': (
        '9ad4d8d38c808735b04581d5a06bb5c959a0cce292fbc11c3d84af106f9e306f',
    ),
    'c9f57b42d0b3300bca51c1ee92e76fcf3dc021c6889bcad743d989e6201c59a6': (
        '675902ec570e62a585ba33f1f62f8c78d6f03d221550f51ff1f132b88fa204ce',
    ),
    'cb9d4280428f6b59f22b1a733b233796643de1793a9d85cc25f1721177c843b3': (
        '99293097066017e7208642a23116a986383cf22951bead844ba4e8d95dc07c8f',
    ),
    'cd4ed3671db501d7009a365da722cb32c5a8ccc210b8760f1743d143e43dc8cf': (
        'fc913be82f17d3c863e991b0da2ea6950028564669f4f2a613530b9de7525359',
    ),
    'd40b1efc03e0402552bc839529dbfacc302801d4d9fc18de98df3bf05b6dddc9': (
        'd010c39f7c5eb89c7397dda1af7e6936e5c23a03c5dd6e201706d1e40589a957',
    ),
    'd7e41c193370a5ade03c9e4642f5161590bf2e05f606e3bf7f92880e48106304': (
        '55806f631f4c493c592f192517ff7308bfeae50ef6359934f75d19d9ded45243',
    ),
    'd978a3bc282e2a2dd5e5420a116d15aa90b4a2d7030e6622ef65ef4c0d9d8037': (
        '71bd648055600c94c9e52941e26cec7bdde951cb5c0b8ba6f50586a8c01f56ab',
        'cffae84ee902e6fc7042c95a7985e3d30debbb7936b323cbb6030f74b3015518',
    ),
    'dae47f413540483c412188f6469cd042170cc1faa464a42a692e729dc6410d35': (
        'b21b21eeec547c2c23a0368264da067564a506673c0711a3528d405209568adf',
    ),
    'df8b716ff1cbab8920560271b3a384606cfd4da1bbe42cdc2988217d9756fdf2': (
        '1d96c2d0f3f44e8dc2248c58f051c2693aeca0b935a0055c993aa1332b382444',
        '5cb68ad3511d528b8efc8fa02fc147c37679fd6a6080173d47334995633f2732',
    ),
    'e0197bf8046a19d9547f947168594098e0f0eef9465fd3cf0a33bdd977c1a7a3': (
        '3ad7719b8dc49daa51b71a394209b56370be1f3f9ef0408a7fd7f15bdec37a90',
    ),
    'ea3d08d20ef8812a7f9ec0147a5312410089639ff085d5c6ac874c599fe874fd': (
        'e4d7454603f848bcd380372b93120213aedc0cce807f52728a21b8724667003e',
    ),
    'eab88de1fb263da9dd6a3a21e2c9bcc4e56f2749dd09befdf992781c82b8730f': (
        '779f06ddd34ba0b877cf9582ac70de275412351613b5b2be5bbde09d21878440',
    ),
    'ebc4b50f376100cf3387edd738632b10aa6c932bd09d106b0b1a50194ff2614f': (
        'cba23be330ffa9be02129fb3a9963572e4a79fa148ec71c886299237d3f22e11',
    ),
    'f0f28a8a54a49a31a8b0ba08a489050a136d680fa4df7c0976ce5768fbc2839b': (
        '16eca2174799c055148706344f8f6d7caad8b675e08f594f7b51d45a11bf2cc8',
    ),
    'fe3ad8776558b51f03a014951eea17eb80d53fd8e82951c6c1ac634215ab2ead': (
        'b9c7458e3f3b25d45c8e564f5cea95147afe036086a27cbf659443b83e983344',
    ),
}


# Bundled skills whose content was corrected in-place (not renamed). seed_skills()
# never overwrites existing files, so without this map a user on the pre-fix
# revision keeps the stale copy forever. Each entry lists every digest the file
# shipped with before the fix, on both POSIX (LF) and Windows (CRLF) line
# endings — seed_skills() compares bytes, and a CRLF checkout hashes differently.
# User-customized files (any digest not listed here) are always preserved.
BUNDLED_SKILL_UPDATE_DIGESTS: dict[str, tuple[str, ...]] = {
    "zeline-zenith-z0.md": (
        # origin/main pre-fix, LF
        "577d36a35e97b4c461e769c723dc4a6187e99dd4646c5584f59e7d759be67a09",
        # same content with CRLF (Windows checkout)
        "629a599da79b90c6016d739ba19fe70afb8d7d79d56649507ef36d2767c7ba9a",
    ),
    "zeline-zenith-z52.md": (
        # origin/main pre-fix, LF
        "9afdaf5bf7613db366046418fb07cc90952ece1734c88d2aa4e839fefa39f0e6",
        # same content with CRLF (Windows checkout)
        "3bc375a999d48666cf809245298864710879ba4a6a799a617595ddec06114b78",
    ),
}


def _safe_name(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "-")
    normalized = "".join(char for char in normalized if char.isalnum() or char in "-_")
    if not _NAME_RE.fullmatch(normalized):
        raise ValueError("nama skill harus 1–64 karakter: huruf, angka, - atau _")
    return normalized


def _chmod_private(path: Path, mode: int = 0o700) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _migrate_legacy_root() -> None:
    """Pindah markdown root lama ke private, tanpa menghapus data pemilik."""
    if MIGRATION_MARKER.exists():
        return
    for old_path in SKILLS_ROOT.glob("*.md"):
        name = old_path.stem
        destination = PRIVATE_SKILLS_DIR / old_path.name
        if destination.exists():
            # Tidak pernah overwrite: beri nama baru deterministik-ish.
            suffix = int(time.time() * 1000)
            destination = PRIVATE_SKILLS_DIR / f"{name}-legacy-{suffix}.md"
        old_path.replace(destination)
        _chmod_private(destination, 0o600)
    MIGRATION_MARKER.write_text("scoped skill migration complete\n", encoding="utf-8")
    _chmod_private(MIGRATION_MARKER, 0o600)


def _ensure_dirs() -> None:
    for directory in (SKILLS_ROOT, PUBLIC_SKILLS_DIR, PRIVATE_SKILLS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        _chmod_private(directory)
    _migrate_legacy_root()


def _remove_unmodified_legacy_bundled_skills() -> None:
    """Remove obsolete seeded copies without deleting user customizations."""
    public_root = PUBLIC_SKILLS_DIR.resolve()

    def remove_if_untouched(path: Path, expected_digests: tuple[str, ...]) -> None:
        if path.is_symlink():
            return
        try:
            resolved = path.resolve(strict=False)
            # Windows runners may spell one directory as DOS 8.3 vs canonical
            # long path. Compare filesystem identity, not path-string spelling.
            if not resolved.parent.samefile(public_root) or not path.is_file():
                return
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in expected_digests:
                path.unlink()
        except OSError:
            pass

    for name, expected_digests in LEGACY_BUNDLED_SKILL_DIGESTS.items():
        # Migration entries are flat bundled Markdown filenames, never paths.
        if Path(name).name == name and name.endswith(".md"):
            remove_if_untouched(PUBLIC_SKILLS_DIR / name, expected_digests)

    for path in PUBLIC_SKILLS_DIR.glob("*.md"):
        filename_digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()
        expected_digests = RETIRED_BUNDLED_SKILL_DIGESTS.get(filename_digest)
        if expected_digests:
            remove_if_untouched(path, expected_digests)


def _refresh_known_bundled_revisions(source: Path) -> int:
    """Overwrite seeded copies that still match a known pre-fix revision.

    seed_skills() never overwrites existing files, which is the right default
    for user customizations. But when a bundled skill is corrected in-place
    (not renamed), a user on the old revision keeps the stale copy forever.
    This hook removes that copy before seeding so the fresh source replaces it.

    Only files whose current bytes match a digest in BUNDLED_SKILL_UPDATE_DIGESTS
    are touched. A user-customized file (any other digest) is always preserved.
    Returns the number of stale copies removed.
    """
    if not BUNDLED_SKILL_UPDATE_DIGESTS:
        return 0
    public_root = PUBLIC_SKILLS_DIR.resolve()
    removed = 0
    for name, expected_digests in BUNDLED_SKILL_UPDATE_DIGESTS.items():
        # Only refresh files that actually ship from the current source.
        if not (source / name).is_file():
            continue
        path = PUBLIC_SKILLS_DIR / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=False)
            if not resolved.parent.samefile(public_root):
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in expected_digests:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def seed_skills(source: str | Path | None = None) -> int:
    """Salin skill bawaan dari paket ke scope public tanpa overwrite.

    Mendukung dua bentuk sumber:
    - flat  : ``skills/<name>.md``
    - folder : ``skills/<name>/SKILL.md`` (+ references/scripts/assets)

    ``source`` is injectable for importers/tests so they never need to mutate
    the installed package directory. Normal callers use the bundled skills.
    """
    _ensure_dirs()
    _remove_unmodified_legacy_bundled_skills()
    source = Path(source).expanduser().resolve() if source is not None else Path(__file__).resolve().parent / "skills"
    if not source.exists():
        return 0
    _refresh_known_bundled_revisions(source)
    copied = 0
    # 1) skill flat: satu file .md langsung di root skills/
    for item in source.glob("*.md"):
        destination = PUBLIC_SKILLS_DIR / item.name
        if not destination.exists():
            destination.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            _chmod_private(destination, 0o600)
            copied += 1
    # 2) skill folder: subdirektori berisi SKILL.md (+ file pendukung).
    for sub in sorted(source.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "SKILL.md").is_file():
            continue
        destination = PUBLIC_SKILLS_DIR / sub.name
        if not destination.exists():
            _copy_skill_tree(sub, destination)
            copied += 1
    return copied


def _copy_skill_tree(src: Path, dst: Path) -> None:
    """Salin satu folder skill (SKILL.md + file pendukung) secara aman.

    Symlink diabaikan agar tidak ada path yang keluar dari folder tujuan.
    """
    for root, dirs, files in os.walk(src):
        # Jangan ikuti symlink direktori.
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        _chmod_private(target_dir)
        for name in files:
            source_file = Path(root) / name
            if source_file.is_symlink():
                continue
            target_file = target_dir / name
            try:
                target_file.write_bytes(source_file.read_bytes())
                _chmod_private(target_file, 0o600)
            except OSError:
                pass


def _parse(markdown: str) -> tuple[str, str]:
    title, description = "", ""
    lines = markdown.splitlines()
    # YAML frontmatter (skill folder standar): --- name: .. description: .. ---
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            stripped = lines[index].strip()
            if stripped == "---":
                break
            lower = stripped.lower()
            if not title and lower.startswith("name:"):
                title = stripped.split(":", 1)[1].strip().strip("\"'")
            elif not description and lower.startswith("description:"):
                description = stripped.split(":", 1)[1].strip().strip("\"'")
    # Format Zeline klasik: '# Judul' + '> deskripsi'
    for line in lines:
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
        elif not description and stripped.startswith(">"):
            description = stripped.lstrip("> ").strip()
        if title and description:
            break
    return title, description


def _iter_skill_units(directory: Path) -> list[tuple[str, Path]]:
    """Kembalikan ``(name, skill_md_path)`` untuk skill flat maupun folder."""
    units: list[tuple[str, Path]] = []
    if not directory.exists():
        return units
    for item in sorted(directory.glob("*.md")):
        units.append((item.stem, item))
    for sub in sorted(directory.iterdir()):
        if sub.is_dir() and (sub / "SKILL.md").is_file():
            units.append((sub.name, sub / "SKILL.md"))
    return units


def list_skill_entries(include_private: bool = True) -> list[tuple[str, str, str, str]]:
    """Return ``(scope, name, title, description)`` entries."""
    _ensure_dirs()
    result: list[tuple[str, str, str, str]] = []
    locations: list[tuple[str, Path]] = [("public", PUBLIC_SKILLS_DIR)]
    if include_private:
        locations.append(("private", PRIVATE_SKILLS_DIR))
    for scope, directory in locations:
        for name, skill_md in _iter_skill_units(directory):
            title, description = _parse(skill_md.read_text(encoding="utf-8", errors="replace"))
            result.append((scope, name, title or name, description or "(tanpa deskripsi)"))
    return result


def list_skills(include_private: bool = True) -> list[tuple[str, str, str]]:
    """Compatibility helper: list name/title/description without scope."""
    return [(name, title, description) for _scope, name, title, description in list_skill_entries(include_private)]


def _find_skill(name: str, include_private: bool) -> Path | None | str:
    """Find one safe skill; return error string on ambiguous fuzzy matching.

    Mengembalikan Path ke file .md (flat) atau ke SKILL.md di dalam folder.
    """
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    # Owner private version overrides public version with same name.
    directories = [PUBLIC_SKILLS_DIR]
    if include_private:
        directories.insert(0, PRIVATE_SKILLS_DIR)
    # 1) exact match: flat .md dulu, lalu folder/SKILL.md.
    for directory in directories:
        exact = directory / f"{normalized}.md"
        if exact.is_file():
            return exact
        folder = directory / normalized / "SKILL.md"
        if folder.is_file():
            return folder
    # 2) Alias pendek korpus Zenith. Registry bawaan menggunakan ``z0`` …
    # ``z95``, sedangkan nama file publiknya memakai prefix
    # ``zeline-zenith-``. Tangani ini sebelum fuzzy matching agar ``z1``
    # tidak berbenturan dengan ``z10`` … ``z19``.
    if re.fullmatch(r"z(?:[0-9]|[1-9][0-9])", normalized):
        for directory in directories:
            canonical = directory / f"zeline-zenith-{normalized}.md"
            if canonical.is_file():
                return canonical
    # 3) fuzzy match berdasar nama unit skill.
    candidates: list[Path] = []
    for directory in directories:
        for unit_name, skill_md in _iter_skill_units(directory):
            if normalized in unit_name.lower():
                candidates.append(skill_md)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = [p.parent.name if p.name == "SKILL.md" else p.stem for p in candidates[:8]]
        return "ERROR ambiguous skill: " + ", ".join(names)
    return None


def load_skill(name: str, include_private: bool = False) -> str:
    """Muat skill scope yang diizinkan. Input bukan path filesystem.

    Untuk skill folder, isi SKILL.md dikembalikan plus daftar file pendukung
    (references/scripts/assets) agar agent tahu file yang bisa dibaca.
    """
    _ensure_dirs()
    found = _find_skill(name, include_private=include_private)
    if isinstance(found, str):
        return found
    if found is None:
        try:
            normalized = _safe_name(name)
        except ValueError:
            normalized = name.strip()
        return f"ERROR: skill '{normalized}' not found."
    content = found.read_text(encoding="utf-8", errors="replace")
    # Skill folder: sertakan daftar file pendukung relatif ke folder skill.
    if found.name == "SKILL.md":
        skill_dir = found.parent
        extras: list[str] = []
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and path != found and not path.is_symlink():
                extras.append(str(path.relative_to(skill_dir)))
        if extras:
            # POSIX-style separators keep the listing stable across platforms;
            # on Windows ``relative_to`` yields backslashes, which broke both the
            # docs the agent reads back and the tests asserting on this text.
            listing = "\n".join(
                f"- {skill_dir.as_posix()}/{Path(rel).as_posix()}" for rel in extras
            )
            content += (
                "\n\n---\n### Supporting files for this skill (read with read_file if needed):\n"
                + listing
            )
    return content


def _describe(path: Path) -> str:
    """Sebut artefak yang benar-benar ditulis, relatif ke root skill.

    Pesan sukses lama berbunyi ``Patched SKILL.md`` untuk file flat
    ``private/<name>.md``; teksnya meniru agent lain sedangkan artefaknya beda,
    jadi operator tidak bisa memercayai laporan self-improvement.
    """
    try:
        return path.relative_to(SKILLS_ROOT).as_posix()
    except ValueError:
        return path.name


def _safe_skill_relative(skill_dir: Path, relative: str) -> Path:
    """Resolusi ``relative`` di dalam folder skill, dengan bukti containment.

    Allowlist karakter saja hanya berargumen "nama ini terlihat wajar"; properti
    yang sebenarnya dibutuhkan adalah "tidak ada file yang pernah dibuat di luar
    folder skill". Jadi path digabung, dinormalisasi, lalu containment-nya
    dibuktikan langsung.
    """
    cleaned = str(relative or "").strip().replace("\\", "/").lstrip("/")
    parts = [segment for segment in cleaned.split("/") if segment not in ("", ".")]
    if not parts:
        raise ValueError("file_path kosong")
    for segment in parts:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", segment):
            raise ValueError(f"segmen file_path tidak valid: {segment!r}")
    if len(parts) == 1:
        if parts[0] != SKILL_ENTRY:
            raise ValueError(f"file di root skill harus {SKILL_ENTRY}")
    elif parts[0] not in SKILL_SUBDIRS:
        raise ValueError("file pendukung harus di " + "/, ".join(SKILL_SUBDIRS) + "/")
    target = skill_dir.joinpath(*parts)
    base = skill_dir.resolve(strict=False)
    resolved = target.resolve(strict=False)
    if base != resolved and base not in resolved.parents:
        raise ValueError("file_path keluar dari folder skill")
    return target


def _frontmatter(name: str, content: str, category: str = "") -> str:
    """Pastikan skill punya frontmatter YAML (name/description/category).

    Skill yang disimpan model sebelumnya hanya markdown bebas, sehingga daftar
    skill di system prompt sering kehilangan deskripsi. ``_parse`` sudah membaca
    frontmatter, jadi menuliskannya membuat katalog konsisten.
    """
    body = content.strip("\n")
    if body.startswith("---"):
        return body + "\n"
    title, description = _parse(body)
    lines = ["---", f"name: {name}"]
    if description:
        lines.append("description: " + description.replace("\n", " ").strip())
    if category:
        lines.append(f"category: {category}")
    lines.append("---")
    if not title:
        lines.append("")
        lines.append(f"# {name}")
    return "\n".join(lines) + "\n\n" + body + "\n"


def _private_skill_dir(name: str) -> Path:
    return PRIVATE_SKILLS_DIR / name


def _locate_unit(name: str) -> tuple[str, Path] | None:
    """Cari unit skill persis bernama ``name``: ``(scope, path)``.

    ``path`` adalah folder skill bila berbentuk folder, atau file ``.md`` bila
    flat. Private diperiksa lebih dulu karena menimpa public dengan nama sama.
    """
    for scope, directory in (("private", PRIVATE_SKILLS_DIR), ("public", PUBLIC_SKILLS_DIR)):
        folder = directory / name
        if (folder / SKILL_ENTRY).is_file():
            return scope, folder
        flat = directory / f"{name}.md"
        if flat.is_file():
            return scope, flat
    return None


def _adopt_into_private(name: str) -> tuple[Path, str]:
    """Siapkan folder skill private yang bisa ditulis; kembalikan (dir, catatan).

    ``seed_skills()`` sengaja tidak menimpa, jadi memperbaiki skill bundled di
    tempatnya akan hilang / bentrok pada update berikutnya. Karena itu skill
    public di-copy-on-write ke private (yang berprioritas lebih tinggi di
    ``_find_skill``) sebelum dipatch, dan skill private flat dipromosikan jadi
    folder supaya bisa punya ``references/``.
    """
    target = _private_skill_dir(name)
    located = _locate_unit(name)
    if located is None:
        target.mkdir(parents=True, exist_ok=True)
        _chmod_private(target)
        return target, "created"
    scope, path = located
    if scope == "private" and path.is_dir():
        return path, ""
    if scope == "private":  # flat private → promosikan jadi folder
        body = path.read_text(encoding="utf-8", errors="replace")
        target.mkdir(parents=True, exist_ok=True)
        _chmod_private(target)
        entry = target / SKILL_ENTRY
        entry.write_text(_frontmatter(name, body), encoding="utf-8")
        _chmod_private(entry, 0o600)
        path.unlink()
        return target, "promoted from a flat file"
    # public → copy-on-write
    if path.is_dir():
        _copy_skill_tree(path, target)
        return target, "copied from the bundled skill"
    body = path.read_text(encoding="utf-8", errors="replace")
    target.mkdir(parents=True, exist_ok=True)
    _chmod_private(target)
    entry = target / SKILL_ENTRY
    entry.write_text(_frontmatter(name, body), encoding="utf-8")
    _chmod_private(entry, 0o600)
    return target, "copied from the bundled skill"


def _create_skill(name: str, content: str, category: str) -> str:
    if not content.strip():
        return "ERROR skill: empty content."
    if len(content) > MAX_SKILL_CHARS:
        return f"ERROR skill: content too long (maximum {MAX_SKILL_CHARS:,} characters)."
    if category and not re.fullmatch(r"[a-z0-9][a-z0-9/_-]{0,63}", category):
        return "ERROR skill: category harus huruf kecil, angka, - _ atau /."
    skill_dir = _private_skill_dir(name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    _chmod_private(skill_dir)
    entry = skill_dir / SKILL_ENTRY
    entry.write_text(_frontmatter(name, content, category), encoding="utf-8")
    _chmod_private(entry, 0o600)
    # Sebuah file flat dengan nama sama akan muncul sebagai unit kedua di
    # ``_iter_skill_units`` (satu nama, dua entri katalog), jadi dibuang.
    legacy = PRIVATE_SKILLS_DIR / f"{name}.md"
    if legacy.is_file():
        legacy.unlink()
    return f"OK, skill '{name}' created at {_describe(entry)}."


def _patch_skill(name: str, old_text: str, new_text: str, file_path: str) -> str:
    if not old_text:
        return "ERROR patch skill: old_text kosong."
    skill_dir, note = _adopt_into_private(name)
    if note == "created":
        # Tidak ada yang bisa dipatch; jangan tinggalkan folder kosong.
        with contextlib.suppress(OSError):
            skill_dir.rmdir()
        return f"ERROR: skill '{name}' not found."
    try:
        target = _safe_skill_relative(skill_dir, file_path or SKILL_ENTRY)
    except ValueError as exc:
        return f"ERROR patch skill: {exc}"
    if not target.is_file():
        return f"ERROR: {_describe(target)} not found in skill '{name}'."
    content = target.read_text(encoding="utf-8", errors="replace")
    count = content.count(old_text)
    if count != 1:
        return f"ERROR patch skill: old_text must be unique (found {count})."
    target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    _chmod_private(target, 0o600)
    suffix = f" ({note})" if note else ""
    return f"Patched {_describe(target)} (1 replacement){suffix}."


def _write_skill_file(name: str, file_path: str, content: str) -> str:
    if len(content) > MAX_SKILL_CHARS:
        return f"ERROR skill: content too long (maximum {MAX_SKILL_CHARS:,} characters)."
    skill_dir, note = _adopt_into_private(name)
    try:
        target = _safe_skill_relative(skill_dir, file_path)
    except ValueError as exc:
        return f"ERROR skill file: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    _chmod_private(target.parent)
    target.write_text(content, encoding="utf-8")
    _chmod_private(target, 0o600)
    suffix = f" ({note})" if note and note != "created" else ""
    return f"Wrote {_describe(target)} ({len(content):,} chars){suffix}."


def _delete_skill(name: str, absorbed_into: str) -> str:
    located = _locate_unit(name)
    if located is None:
        return f"ERROR: skill '{name}' not found."
    scope, path = located
    if scope != "private":
        return (
            f"ERROR: '{name}' is a bundled/public skill; patch it instead "
            "(the patch is copied into private scope automatically)."
        )
    if absorbed_into:
        try:
            merged = _safe_name(absorbed_into)
        except ValueError as exc:
            return f"ERROR skill: {exc}"
        if merged == name:
            return "ERROR skill: absorbed_into tidak boleh skill itu sendiri."
        if _locate_unit(merged) is None:
            return (
                f"ERROR: absorbed_into target '{merged}' does not exist yet — "
                "write the umbrella skill first, then delete this one."
            )
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink()
    if absorbed_into:
        return f"Deleted private skill '{name}' (absorbed into '{absorbed_into}')."
    return f"Deleted private skill '{name}' (no forwarding target)."


def _inventory() -> str:
    """Daftar skill + bentuknya, supaya duplikat bisa dicek sebelum menyimpan."""
    entries = list_skill_entries(include_private=True)
    if not entries:
        return "No skills yet."
    lines = []
    for scope, unit, _title, description in entries:
        located = _locate_unit(unit)
        shape = "folder" if located and located[1].is_dir() else "flat"
        lines.append(f"- {unit} [{scope}/{shape}]: {_short_desc(description)}")
    return f"{len(lines)} skills:\n" + "\n".join(lines)


def manage_skill(
    action: str,
    name: str = "",
    content: str = "",
    old_text: str = "",
    new_text: str = "",
    file_path: str = "",
    category: str = "",
    absorbed_into: str = "",
) -> str:
    """Satu permukaan tulis untuk skill milik operator.

    Aksi: ``create`` (folder + ``SKILL.md`` + frontmatter), ``patch`` (file mana
    pun di dalam folder skill; skill bundled di-copy-on-write ke private lebih
    dulu), ``write_file`` (``references/`` · ``templates/`` · ``scripts/`` ·
    ``assets/``), ``delete`` (dengan ``absorbed_into`` untuk menggabungkan
    duplikat), dan ``list`` (inventaris untuk cek duplikat).

    Sebelumnya hanya ada ``save_skill``/``update_skill``: satu file markdown flat
    tanpa struktur, tanpa hapus, dan tak mampu menyentuh skill bundled — sehingga
    refleksi hanya bisa menumpuk file baru dan duplikat menjadi hasil default.
    """
    _ensure_dirs()
    verb = str(action or "").strip().lower()
    if verb in {"list", "inventory"}:
        return _inventory()
    if verb not in {"create", "patch", "write_file", "delete"}:
        return (
            f"ERROR skill: unknown action {action!r} "
            "(create, patch, write_file, delete, list)."
        )
    try:
        normalized = _safe_name(name)
    except ValueError as exc:
        return f"ERROR skill: {exc}"
    if verb == "create":
        return _create_skill(normalized, content, category.strip().lower())
    if verb == "patch":
        return _patch_skill(normalized, old_text, new_text, file_path)
    if verb == "write_file":
        return _write_skill_file(normalized, file_path, content)
    return _delete_skill(normalized, absorbed_into.strip())


def save_skill(name: str, content: str) -> str:
    """Kompatibilitas: sekarang membuat skill berbentuk folder."""
    return manage_skill("create", name=name, content=content)


def update_skill(name: str, old_text: str, new_text: str) -> str:
    """Kompatibilitas: patch ``SKILL.md`` skill bersangkutan."""
    return manage_skill("patch", name=name, old_text=old_text, new_text=new_text)


def _short_desc(description: str, limit: int = 90) -> str:
    """Deskripsi ringkas 1-baris untuk daftar skill di system prompt.

    Daftar skill di-inject SETIAP turn; deskripsi panjang (ratusan char × 171
    skill ≈ 20k char) memboroskan token & memperlambat tiap request. Cukup
    kalimat pertama / potong di ~90 char — nama skill tetap bisa ditemukan,
    isi lengkap dibaca via load_skill saat relevan.
    """
    text = " ".join(str(description).split())
    # Ambil kalimat pertama bila pendek; kalau tidak, potong keras di limit.
    for sep in (". ", " — ", "; "):
        head = text.split(sep, 1)[0]
        if head and len(head) <= limit:
            return head
    return text[:limit].rstrip(" ,.—-") + ("…" if len(text) > limit else "")


def skills_block(include_private: bool = False) -> str:
    """Daftar token-cheap untuk system prompt sesuai otorisasi session."""
    available = list_skill_entries(include_private=include_private)
    if not available:
        return ""
    lines = "\n".join(
        f"- {name}: {_short_desc(description)}" if scope == "public" else f"- {name} [private]: {_short_desc(description)}"
        for scope, name, _title, description in available
    )
    return "\n\n## Available skills (call load_skill for full content):\n" + lines
