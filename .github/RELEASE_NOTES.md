## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Highlights

- **Sanitized public skill package** — removes personal account examples, payment identifiers, infrastructure details, local router state, and session-specific deployment notes from bundled skills.
- **Normalized Zenith references** — fixes duplicated `zeline-zenith-` prefixes across the bundled compatibility corpus without renaming stable skill IDs.
- **Reliable public documentation** — replaces unresolved website links with the repository documentation that is available now.
- **Future-proof release validation** — artifact checks now derive the package version from the verified release tag instead of hardcoding a version.
- **Security reporting enabled** — vulnerabilities can be reported privately through GitHub Security Advisories.
- Versioned, checksum-verified installers for Termux, Linux, macOS, iSH, and Windows PowerShell.
- Immutable release artifacts with build provenance.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.4/docs/installation.md) for checksum-verified commands on every supported platform.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
