% Bundle a single prepared package directory using `mip bundle`.
%
% Usage (from repo root, after addpath('mip'); addpath('scripts')):
%   bundle_one
%
% Expects:
%   - exactly one directory under build/prepared/ (the one written by
%     scripts/prepare_one.py)
%   - $BUILD_ARCHITECTURE set to the target architecture
%
% Produces: build/bundled/<name>-<version>-<arch>.mhl (+ .mip.json)

fprintf('=== bundle_one ===\n');

preparedDir = fullfile(pwd, 'build', 'prepared');
outputDir = fullfile(pwd, 'build', 'bundled');

architecture = getenv('BUILD_ARCHITECTURE');
if isempty(architecture)
    error('mip:missingArchitecture', ...
        'Environment variable BUILD_ARCHITECTURE is not set');
end

if ~exist(preparedDir, 'dir')
    error('mip:noPrepared', ...
        'No prepared directory at %s', preparedDir);
end

if ~exist(outputDir, 'dir')
    mkdir(outputDir);
end

items = dir(preparedDir);
items = items(~startsWith({items.name}, '.') & [items.isdir]);
if isempty(items)
    error('mip:noPreparedSubdir', ...
        'build/prepared/ contains no package subdirectory');
end
if numel(items) > 1
    names = strjoin({items.name}, ', ');
    error('mip:multiplePrepared', ...
        'bundle_one expects exactly one prepared dir, found: %s', names);
end

pkgDir = fullfile(preparedDir, items(1).name);
if ~exist(fullfile(pkgDir, 'mip.yaml'), 'file')
    error('mip:noMipYaml', 'No mip.yaml in %s', pkgDir);
end

fprintf('Setting up MEX compilers...\n');
compiler = resolve_compiler(pkgDir, architecture);
setup_mex_compilers(architecture, compiler);

fprintf('Bundling: %s (arch=%s)\n', items(1).name, architecture);
mip.bundle(pkgDir, '--output', outputDir, '--arch', architecture);
fprintf('Bundle OK\n');


function compiler = resolve_compiler(pkgDir, architecture)
% Read the MEX compiler for this architecture from the package's mip.yaml.
%
% A build entry may carry an optional `compiler` field: a mapping from
% architecture name to compiler name (e.g. compiler.windows_x86_64: msvc).
% Architectures not listed -- and packages with no `compiler` field -- use
% the architecture default baked into setup_mex_compilers. Returns '' to
% mean "use the default".
    compiler = '';
    mipConfig = mip.config.read_mip_yaml(pkgDir);
    [buildEntry, effectiveArch] = mip.build.match_build(mipConfig, architecture);
    resolved = mip.build.resolve_build_config(mipConfig, buildEntry);
    if ~isfield(resolved, 'compiler')
        return
    end
    spec = resolved.compiler;
    if ~isstruct(spec)
        error('mip:bundleOne:badCompiler', ...
              ['mip.yaml build "compiler" must be a mapping from architecture ' ...
               'to compiler name (e.g. "compiler:\n  windows_x86_64: msvc").']);
    end
    if isfield(spec, effectiveArch)
        compiler = spec.(effectiveArch);
    end
end
