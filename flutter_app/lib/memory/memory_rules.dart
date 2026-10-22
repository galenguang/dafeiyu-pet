import 'memory_store.dart';

/// 候选的类别。
enum CandKind {
  callMe,
  occupation,
  like,
  dislike,
  habit,
  fact,
  episode,
  lateNight,
}

class Candidate {
  final CandKind kind;
  final String text;
  final double confidence;
  final String episodeKind;
  const Candidate(
    this.kind,
    this.text, {
    this.confidence = 0.7,
    this.episodeKind = 'event',
  });
}

/// 本地规则轨：**永远跑**，零成本零延迟。
///
/// 对应桌面版 `app/memory/rules.py`。模型轨要花 token、还可能失败，
/// 而「我叫小明」「我喜欢咖啡」这种最该记住的信息，正则就能抓住。
/// 规则先跑完，模型只做补充，两条轨互不依赖。
class MemoryRules {
  /// 抽出来的宾语是这些就没意义（"我喜欢你"不该记成喜欢"你"）。
  static const _stopObjects = {
    '你',
    '您',
    '我',
    '他',
    '她',
    '它',
    '这个',
    '那个',
    '什么',
    '啥',
    '东西',
    '事情',
    '感觉',
    '样子',
    '时候',
    '一点',
    '有点',
    '这样',
    '那样',
    '一下',
    '一个',
    '真的',
    '非常',
    '特别',
  };

  static final _leadVerb = RegExp(r'^(喝|吃|玩|看|听|打|买|用|搞|做|抽|养|穿|读|学)');
  static final _tailParticle = RegExp(r'[了啊呀吧呢哦嘛哈呗啦噢哟]+$');
  static final _sentenceSplit = RegExp(r'[。！？!?\n;；]+');
  static final _clauseSplit = RegExp(r'[，,、]+');

  static final _rCallMe = RegExp(
    r'(?:叫我|喊我|称呼我|你可以叫我|你可以喊我)\s*([^\s，,。！？!?]{1,8})',
  );
  static final _rMyName = RegExp(r'^我(?:的名字)?叫\s*([^\s，,。！？!?]{1,8})');
  static final _rJob = RegExp(
    r'我(?:是|在做|干|搞)(?:一?[个名])?(?:程序员|工程师|设计师|产品经理|学生|老师|医生|护士|律师|会计|销售|运营|编辑|记者|司机|厨师|保安|店主|老板|自由职业)',
  );
  static final _rJobBare = RegExp(r'我(?:的)?(?:职业|工作)是\s*([^，,。！？!?\n]{2,12})');
  static final _rLike = RegExp(
    r'我(?:很|超|超级|特别|最|挺|真的|也|还)?(?:喜欢|爱|中意|钟意)\s*([^，,。！？!?\n]{1,14})',
  );
  static final _rDislike = RegExp(
    r'我(?:很|超|超级|特别|最|挺|真的|也|还)?(?:不喜欢|讨厌|烦|受不了|厌恶)\s*([^，,。！？!?\n]{1,14})',
  );
  static final _rRemember = RegExp(
    r'(?:记住|记一下|记着|别忘了|别忘|帮我记)[:：,，]?\s*([^。！？!?\n]{2,30})',
  );
  static final _rDoing = RegExp(
    r'我(?:最近|正在|在)(?:做|学|写|弄|搞|准备|研究)\s*([^，,。！？!?\n]{2,14})',
  );
  static final _rLateNight = RegExp(r'(?:熬夜|通宵|没睡|失眠|睡不着)');

  /// 不带主语的版本。
  ///
  /// 为什么必须有：「我喜欢咖啡，讨厌加班」拆成两个分句之后，
  /// 第二句「讨厌加班」里没有「我」，只靠带主语的正则会漏掉——
  /// 而人说话时省略主语才是常态。
  static final _rLikeBare = RegExp(
    r'^(?:很|超|超级|特别|最|挺|真的)?(?:喜欢|爱|中意)\s*([^，,。！？!?\n]{1,14})',
  );
  static final _rDislikeBare = RegExp(
    r'^(?:很|超|超级|特别|最|挺|真的)?(?:不喜欢|讨厌|烦|受不了|厌恶)\s*([^，,。！？!?\n]{1,14})',
  );

  static String _cleanObject(String s, [int maxLen = 12]) {
    var t = s.trim().replaceAll(_tailParticle, '');
    t = t.replaceFirst(_leadVerb, '');
    t = t.replaceAll(RegExp(r'^[的了]'), '');
    if (t.length > maxLen) t = t.substring(0, maxLen);
    return t.trim();
  }

  static bool _okObject(String t) {
    if (t.length < 2) return false;
    if (_stopObjects.contains(t)) return false;
    // 纯标点/纯数字没有记忆价值
    if (!RegExp(r'[\u4e00-\u9fa5A-Za-z]').hasMatch(t)) return false;
    return true;
  }

  /// 从句子里抽候选。返回的可能是空的——大多数寒暄确实没有可记的。
  static List<Candidate> extract(String raw) {
    final out = <Candidate>[];
    final text = raw.trim();
    if (text.isEmpty) return out;

    // 整句先看「记住…」和「叫我…」，这两类优先级最高
    for (final m in _rRemember.allMatches(text)) {
      final t = _cleanObject(m.group(1) ?? '', 30);
      if (t.length >= 2) {
        out.add(Candidate(CandKind.fact, t, confidence: 0.95));
      }
    }
    for (final m in _rCallMe.allMatches(text)) {
      final t = (m.group(1) ?? '').trim();
      if (t.isNotEmpty && t.length <= 8) {
        out.add(Candidate(CandKind.callMe, t, confidence: 0.95));
      }
    }
    for (final m in _rMyName.allMatches(text)) {
      final t = (m.group(1) ?? '').trim();
      if (t.isNotEmpty && t.length <= 8 && !_stopObjects.contains(t)) {
        out.add(Candidate(CandKind.callMe, t, confidence: 0.8));
      }
    }
    if (_rJob.hasMatch(text)) {
      out.add(
        Candidate(
          CandKind.occupation,
          _rJob.firstMatch(text)!.group(0)!.replaceFirst(RegExp(r'^我是?'), ''),
          confidence: 0.8,
        ),
      );
    }
    for (final m in _rJobBare.allMatches(text)) {
      final t = _cleanObject(m.group(1) ?? '', 16);
      if (t.length >= 2) {
        out.add(Candidate(CandKind.occupation, t, confidence: 0.85));
      }
    }

    // 逐句逐分句扫喜好，避免「我喜欢咖啡，讨厌加班」只抓到一个
    for (final sent in text.split(_sentenceSplit)) {
      if (sent.trim().isEmpty) continue;

      if (_rLateNight.hasMatch(sent)) {
        out.add(Candidate(CandKind.lateNight, '熬夜', confidence: 0.75));
      }

      for (final rawClause in sent.split(_clauseSplit)) {
        final clause = rawClause.trim();
        if (clause.isEmpty) continue;

        // 先看否定，否则「我不喜欢咖啡」会被 _rLike 抓成喜欢「咖啡」
        var hit = false;
        for (final m in _rDislike.allMatches(clause)) {
          final t = _cleanObject(m.group(1) ?? '');
          if (_okObject(t)) {
            out.add(Candidate(CandKind.dislike, t, confidence: 0.85));
            hit = true;
          }
        }
        if (hit) continue;

        for (final m in _rLike.allMatches(clause)) {
          final t = _cleanObject(m.group(1) ?? '');
          if (_okObject(t)) {
            out.add(Candidate(CandKind.like, t, confidence: 0.85));
            hit = true;
          }
        }
        if (hit) continue;

        // 省主语的说法：「讨厌加班」「喜欢猫」
        final d = _rDislikeBare.firstMatch(clause);
        if (d != null) {
          final t = _cleanObject(d.group(1) ?? '');
          if (_okObject(t)) {
            out.add(Candidate(CandKind.dislike, t, confidence: 0.75));
            continue;
          }
        }
        final l = _rLikeBare.firstMatch(clause);
        if (l != null) {
          final t = _cleanObject(l.group(1) ?? '');
          if (_okObject(t)) {
            out.add(Candidate(CandKind.like, t, confidence: 0.75));
            continue;
          }
        }

        for (final m in _rDoing.allMatches(clause)) {
          final t = _cleanObject(m.group(1) ?? '', 14);
          if (t.length >= 2) {
            out.add(
              Candidate(
                CandKind.episode,
                '在$t',
                confidence: 0.7,
                episodeKind: 'event',
              ),
            );
          }
        }
      }
    }
    return out;
  }

  /// 把候选写进 store。返回真正新增了几条（重复的不算）。
  static int apply(
    MemoryStore store,
    List<Candidate> cands, {
    String source = 'chat',
  }) {
    var added = 0;
    for (final c in cands) {
      switch (c.kind) {
        case CandKind.callMe:
          store.setCallMe(c.text);
          break;
        case CandKind.occupation:
          store.setOccupation(c.text);
          break;
        case CandKind.like:
          if (store.addLike(c.text)) added++;
          break;
        case CandKind.dislike:
          if (store.addDislike(c.text)) added++;
          break;
        case CandKind.habit:
          if (store.addHabit(c.text)) added++;
          break;
        case CandKind.fact:
          if (store.addFact(
            c.text,
            confidence: c.confidence,
            source: source,
            pinned: true,
          )) {
            added++;
          }
          break;
        case CandKind.episode:
          if (store.addEpisode(
            c.text,
            kind: c.episodeKind,
            weight: c.confidence,
          )) {
            added++;
          }
          break;
        case CandKind.lateNight:
          if (store.addEpisode(
            '又熬夜了',
            kind: 'late_night',
            mood: 'concerned',
            weight: 0.7,
          )) {
            added++;
          }
          break;
      }
    }
    if (added > 0) store.bumpIntimacy(added.clamp(0, 3));
    return added;
  }
}
