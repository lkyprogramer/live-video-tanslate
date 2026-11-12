#!/usr/bin/env python3
"""Offline arithmetic/structure helper. No inference, truth verification or release gate."""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import urlsplit


class EvidenceError(ValueError):
    """Input violates the documented structure or invariant."""


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def envelope(command, **fields):
    return dict(command=command, release_eligible=False,
                scope='arithmetic_or_structure_only', **fields)


def fields(obj, names, context):
    require(isinstance(obj, dict), f'{context}: expected object')
    require(set(obj) == set(names.split()), f'{context}: expected exactly fields {names}')


def integer(value, name):
    require(type(value) is int and value >= 0, f'{name}: expected nonnegative integer (not bool)')


def number(value, name):
    require(type(value) in (int, float), f'{name}: expected finite nonnegative number (not bool)')
    require((type(value) is int or math.isfinite(value)) and value >= 0,
            f'{name}: expected finite nonnegative number')


def nonempty(value, name):
    require(isinstance(value, str) and bool(value.strip()), f'{name}: expected nonempty string')


def version(data, names, data_kind=True):
    fields(data, names, 'input')
    require(type(data['schema_version']) is int and data['schema_version'] == 1,
            'schema_version: expected integer 1')
    if data_kind:
        require(data['data_kind'] in ('synthetic', 'measured'), 'data_kind: expected synthetic or measured')


def rows(value, name, nonempty_required=False):
    require(isinstance(value, list), f'{name}: expected array')
    if nonempty_required:
        require(bool(value), f'{name}: empty array is not evidence')


def nearest_rank(values, percentile):
    return sorted(values)[math.ceil(percentile * len(values)) - 1] if values else None


def timing(data):
    """Validate supplied timestamps; summarize sentence-end latencies in milliseconds."""
    version(data, 'schema_version data_kind units')
    rows(data['units'], 'units', True)
    seen, output, matched, all_latencies = set(), [], [], []
    missing = late = 0
    for index, unit in enumerate(data['units']):
        where = f'units[{index}]'
        fields(unit, 'unit_id source_start_ms source_end_ms stable_correct_ms', where)
        nonempty(unit['unit_id'], where+'.unit_id')
        require(unit['unit_id'] not in seen, where+': duplicate unit_id')
        seen.add(unit['unit_id'])
        for key in ('source_start_ms', 'source_end_ms'):
            number(unit[key], where+'.'+key)
        require(unit['source_start_ms'] <= unit['source_end_ms'], where+': source_start_ms exceeds source_end_ms')
        stable = unit['stable_correct_ms']
        if stable is None:
            latency, status = None, 'missing'
            missing += 1
            all_latencies.append(math.inf)
        else:
            number(stable, where+'.stable_correct_ms')
            latency = stable - unit['source_end_ms']
            require(type(latency) is int or math.isfinite(latency), where+': derived latency overflows')
            if latency <= 15000:
                status = 'matched_within_15000_ms'
                matched.append(latency)
                all_latencies.append(latency)
            else:
                status = 'late'
                late += 1
                all_latencies.append(math.inf)
        output.append(dict(unit_id=unit['unit_id'], latency_ms=latency, status=status))
    count = len(output)
    coverage = {}
    for cutoff in (2000, 3000, 5000, 15000):
        hit = sum(latency <= cutoff for latency in matched)
        coverage[f'{cutoff}_ms'] = dict(count=hit, fraction=hit/count)
    p95 = nearest_rank(all_latencies, .95)
    unbounded = p95 == math.inf
    return envelope('timing', data_kind=data['data_kind'], unit_count=count,
                    matched_count=len(matched), missing_count=missing, late_count=late,
                    coverage=coverage, units=output,
                    matched_within_15000_ms=dict(count=len(matched),
                        p50_ms=nearest_rank(matched, .50), p95_ms=nearest_rank(matched, .95)),
                    overall_p95=dict(latency_ms=None if unbounded else p95, unbounded=unbounded),
                    limitations=[
                        'stable_correct_ms and unit matching are supplied annotations, not verified translation truth.',
                        'A single calibrated clock is a required input precondition, not verified by this tool.',
                        'No model was run and no quality, resource or release decision is made.'])


def prefix_audit(data):
    """Audit normalized cumulative committed-text snapshots in input order."""
    version(data, 'schema_version data_kind records')
    rows(data['records'], 'records', True)
    streams, scopes = {}, {}
    for index, record in enumerate(data['records']):
        where = f'records[{index}]'
        fields(record, 'scope_id channel sequence_id accepted_samples consumed_samples source_end_sample text', where)
        nonempty(record['scope_id'], where+'.scope_id')
        require(record['channel'] in ('ja', 'zh'), where+'.channel: expected ja or zh')
        require(isinstance(record['text'], str), where+'.text: expected string')
        for key in ('sequence_id', 'accepted_samples', 'consumed_samples', 'source_end_sample'):
            integer(record[key], where+'.'+key)
        accepted, consumed, end = (record[k] for k in ('accepted_samples', 'consumed_samples', 'source_end_sample'))
        require(end <= consumed, where+': source_end_sample exceeds consumed_samples (future source range)')
        require(consumed <= accepted, where+': consumed_samples exceeds accepted_samples')
        scope, channel = record['scope_id'], record['channel']
        if scope in scopes:
            prev_accepted, prev_consumed = scopes[scope]
            require(accepted >= prev_accepted, where+': accepted_samples watermark regressed within scope')
            require(consumed >= prev_consumed, where+': consumed_samples watermark regressed within scope')
        stream = (scope, channel)
        if stream in streams:
            sequence, text, previous_end = streams[stream]
            require(record['sequence_id'] > sequence, where+': sequence_id must strictly increase within scope/channel')
            require(record['text'].startswith(text), where+': prefix invariant violated; committed text was rewritten')
            require(end >= previous_end, where+': source_end_sample regressed within scope/channel')
        scopes[scope] = (accepted, consumed)
        streams[stream] = (record['sequence_id'], record['text'], end)
    return envelope('prefix-audit', data_kind=data['data_kind'],
                    record_count=len(data['records']), scope_count=len(scopes), stream_count=len(streams),
                    prefix_invariant_holds=True,
                    limitations=[
                        'Input is normalized cumulative committed text, not native protocol deltas; no Unicode normalization is applied.',
                        'Scope ownership, lifecycle completeness and semantic correctness are not verified.',
                        'Sample watermarks are compared as integers only; sample rate is not verified.'])


def hex_value(value, length, name, nullable=False):
    if value is None and nullable:
        return
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{'+str(length)+'}', value) is not None,
            f'{name}: expected {length} hexadecimal characters'+(' or null' if nullable else ''))


def relative_path(value, where):
    nonempty(value, where)
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and '\\' not in value
            and ':' not in value and path.parts and value == path.as_posix(),
            where+': expected normalized nonempty relative path without traversal')
    return path


def verify_file(root, spec):
    """Read one file beneath root; never download or mutate any material."""
    path = root / spec['path']
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            return ['path escapes root through symlink']
        if not resolved.is_file():
            return ['not a regular file']
        issues = []
        if spec['size_bytes'] is not None and resolved.stat().st_size != spec['size_bytes']:
            issues.append('size_bytes mismatch')
        if spec['sha256'] is not None:
            digest = hashlib.sha256()
            with resolved.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != spec['sha256'].lower():
                issues.append('sha256 mismatch')
        return issues
    except (OSError, RuntimeError) as error:
        return ['file unavailable: '+str(error)]


def check_lock(data, root=None):
    """Check lock completeness, optionally read and hash local bytes. No acquisition."""
    version(data, 'schema_version project_revision entries', data_kind=False)
    hex_value(data['project_revision'], 40, 'project_revision')
    rows(data['entries'], 'entries')
    seen_ids, seen_paths, entries = set(), set(), []
    for index, entry in enumerate(data['entries']):
        where = f'entries[{index}]'
        fields(entry, 'id required source_url revision files', where)
        nonempty(entry['id'], where+'.id')
        require(entry['id'] not in seen_ids, where+': duplicate entry id')
        seen_ids.add(entry['id'])
        require(type(entry['required']) is bool, where+'.required: expected boolean')
        require(isinstance(entry['source_url'], str), where+'.source_url: expected HTTPS URL')
        url = urlsplit(entry['source_url'])
        require(url.scheme == 'https' and bool(url.hostname) and not url.username
                and not url.password and not any(c.isspace() for c in entry['source_url']),
                where+'.source_url: expected HTTPS URL without credentials')
        hex_value(entry['revision'], 40, where+'.revision', True)
        rows(entry['files'], where+'.files')
        for file_index, spec in enumerate(entry['files']):
            file_where = f'{where}.files[{file_index}]'
            fields(spec, 'path sha256 size_bytes', file_where)
            relative_path(spec['path'], file_where+'.path')
            require(spec['path'] not in seen_paths, file_where+': duplicate file path')
            seen_paths.add(spec['path'])
            hex_value(spec['sha256'], 64, file_where+'.sha256', True)
            if spec['size_bytes'] is not None:
                integer(spec['size_bytes'], file_where+'.size_bytes')
        entries.append(entry)
    local_root = None
    if root is not None:
        local_root = Path(root).resolve(strict=True)
        require(local_root.is_dir(), 'root: expected existing directory')
    unresolved, optional, mismatches = [], [], []
    for entry in entries:
        pending = []
        if entry['revision'] is None:
            pending.append('revision unknown')
        if not entry['files']:
            pending.append('files empty')
        for spec in entry['files']:
            for key in ('sha256', 'size_bytes'):
                if spec[key] is None:
                    pending.append(spec['path']+': '+key+' unknown')
            if local_root is not None:
                issues = verify_file(local_root, spec)
                if issues:
                    item = dict(id=entry['id'], path=spec['path'], required=entry['required'], reasons=issues)
                    mismatches.append(item)
                    pending.extend(spec['path']+': '+issue for issue in issues)
        if pending:
            (unresolved if entry['required'] else optional).append(dict(id=entry['id'], reasons=pending))
    ready = not unresolved
    return envelope('check-lock', ready=ready, bytes_verified=local_root is not None and ready,
                    required_entry_count=sum(e['required'] for e in entries), entry_count=len(entries),
                    unresolved=unresolved, optional_pending=optional, mismatches=mismatches,
                    limitations=[
                        'ready means lock completeness (and required local bytes matched when root is supplied), not release readiness.',
                        'No source authenticity, model suitability, revision-to-file provenance, license or inference behavior is verified.',
                        'Null never means latest. No files are downloaded or modified.'])


def reject_constant(value):
    raise EvidenceError('JSON nonfinite constant is forbidden: '+value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON object key: '+key)
        result[key] = value
    return result


def load_json(path):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream, parse_constant=reject_constant, object_pairs_hook=unique_object)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EvidenceError(message)


def main(argv=None):
    command = 'unknown'
    try:
        parser = Parser(description=__doc__)
        sub = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
        for name in ('timing', 'prefix-audit', 'check-lock'):
            child = sub.add_parser(name)
            child.add_argument('file')
            if name == 'check-lock':
                child.add_argument('--root')
        args = parser.parse_args(argv)
        command = args.command
        data = load_json(args.file)
        if command == 'timing':
            result = timing(data)
        elif command == 'prefix-audit':
            result = prefix_audit(data)
        else:
            result = check_lock(data, args.root)
        code = 2 if command == 'check-lock' and not result['ready'] else 0
    except (EvidenceError, ValueError, TypeError, OSError, OverflowError, RecursionError) as error:
        result = envelope(command, errors=[str(error)], valid=False)
        code = 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
